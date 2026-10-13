import json, os, csv, io, re, threading, time, random, smtplib, ssl, logging, html as html_lib
from datetime import datetime
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid
from flask import Flask, request, jsonify, Response, send_from_directory
from flask_cors import CORS

app = Flask(__name__, static_folder="static")
CORS(app)

CONFIG_FILE = "sender_config.json"
DEFAULT_TEMPLATE = "<p>Hi {{.FirstName}},</p>\n<p>This email is for {{.Email}}.</p>"
PROVIDERS = [
    ("Gmail",               "smtp.gmail.com",                     465, "SSL",      "https://www.gstatic.com/images/branding/product/2x/gmail_2020q4_48dp.png"),
    ("Gmail (STARTTLS)",    "smtp.gmail.com",                     587, "STARTTLS", "https://www.gstatic.com/images/branding/product/2x/gmail_2020q4_48dp.png"),
    ("Outlook / Office365", "smtp.office365.com",                 587, "STARTTLS", "outlook.com"),
    ("Yahoo",               "smtp.mail.yahoo.com",                465, "SSL",      "yahoo.com"),
    ("Yahoo (STARTTLS)",    "smtp.mail.yahoo.com",                587, "STARTTLS", "yahoo.com"),
    ("SendGrid",            "smtp.sendgrid.net",                  587, "STARTTLS", "sendgrid.com"),
    ("Mailgun",             "smtp.mailgun.org",                   587, "STARTTLS", "mailgun.com"),
    ("SMTP2GO",             "mail.smtp2go.com",                   587, "STARTTLS", "smtp2go.com"),
    ("SMTP2GO (SSL)",       "mail.smtp2go.com",                   465, "SSL",      "smtp2go.com"),
    ("Amazon SES",          "email-smtp.us-east-1.amazonaws.com", 587, "STARTTLS", "aws.amazon.com"),
    ("Amazon SES (SSL)",    "email-smtp.us-east-1.amazonaws.com", 465, "SSL",      "aws.amazon.com"),
    ("ProtonMail",          "smtp.proton.me",                     587, "STARTTLS", "https://cdn.simpleicons.org/protonmail"),
    ("Zoho",                "smtp.zoho.com",                      587, "STARTTLS", "zoho.com"),
    ("Zoho (SSL)",          "smtp.zoho.com",                      465, "SSL",      "zoho.com"),
    ("iCloud Mail",         "smtp.mail.me.com",                   587, "STARTTLS", "https://cdn.simpleicons.org/icloud"),
    ("Postmark",            "smtp.postmarkapp.com",               587, "STARTTLS", "postmarkapp.com"),
    ("SparkPost",           "smtp.sparkpostmail.com",             587, "STARTTLS", "https://cdn.simpleicons.org/sparkpost"),
    ("Mandrill",            "smtp.mandrillapp.com",               587, "STARTTLS", "mandrillapp.com"),
    ("Brevo",               "smtp-relay.brevo.com",               587, "STARTTLS", "https://cdn.simpleicons.org/brevo"),
    ("Mailjet",             "in-v3.mailjet.com",                  587, "STARTTLS", "mailjet.com"),
    ("Custom SMTP",         None,                                 None, None,    None),
]

campaign_state = {"running": False, "log": [], "sent": 0, "failed": 0, "total": 0, "done": False, "error": None, "failed_list": []}

def load_config():
    if os.path.isfile(CONFIG_FILE):
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"profiles": {}, "last_profile": None, "last_campaign": None}

def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)

def parse_delay(val):
    val = str(val).strip()
    if "-" in val:
        parts = val.split("-", 1)
        try:
            lo, hi = float(parts[0]), float(parts[1])
            return (min(lo, hi), max(lo, hi))
        except ValueError:
            pass
    try:
        n = float(val)
        return (n, n)
    except ValueError:
        return (1.0, 1.0)

def extract_name_from_email(email):
    local = email.split("@")[0]
    return local.capitalize(), ""

def deduplicate_recipients(recipients):
    seen, deduped, dupes = set(), [], 0
    for r in recipients:
        key = r["Email"].lower()
        if key in seen:
            dupes += 1
        else:
            seen.add(key)
            deduped.append(r)
    return deduped, dupes

def render(template, recipient):
    body = template
    body = body.replace("{{.FirstName}}", recipient.get("FirstName", ""))
    body = body.replace("{{.LastName}}", recipient.get("LastName", ""))
    body = body.replace("{{.Email}}", recipient.get("Email", ""))
    return body

def connect(host, port, security):
    if security == "SSL":
        ctx = ssl.create_default_context()
        return smtplib.SMTP_SSL(host, port, context=ctx, timeout=30)
    server = smtplib.SMTP(host, port, timeout=30)
    server.ehlo()
    if security == "STARTTLS":
        ctx = ssl.create_default_context()
        server.starttls(context=ctx)
        server.ehlo()
    return server

def html_to_text(value):
    text = re.sub(r"(?is)<(script|style|head)[^>]*>.*?</\1>", "", value)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|h[1-6]|tr|li)>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", "", text)
    text = html_lib.unescape(text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()

def text_to_html(value):
    return "<p>" + html_lib.escape(value).replace("\n\n", "</p><p>").replace("\n", "<br>") + "</p>"

def build_message(sender_name, sender_email, recipient, subject, body, subtype, attachment_path=None):
    domain = sender_email.split("@")[-1] if "@" in sender_email else None
    if attachment_path:
        msg = MIMEMultipart("mixed")
        container = MIMEMultipart("alternative")
    else:
        msg = MIMEMultipart("alternative")
        container = msg

    to_name = f"{recipient.get('FirstName','')} {recipient.get('LastName','')}".strip()
    msg["From"] = formataddr((sender_name, sender_email))
    msg["To"] = formataddr((to_name, recipient["Email"]))
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=domain)
    if sender_email:
        msg["Reply-To"] = formataddr((sender_name, sender_email))
        msg["Sender"] = formataddr((sender_name, sender_email))

    if subtype == "plain":
        text_body = body
        html_body = text_to_html(body)
    else:
        text_body = html_to_text(body) or body
        html_body = body

    container.attach(MIMEText(text_body or " ", "plain", "utf-8"))
    container.attach(MIMEText(html_body, "html", "utf-8"))

    if attachment_path:
        msg.attach(container)
        with open(attachment_path, "rb") as f:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f'attachment; filename="{os.path.basename(attachment_path)}"')
        msg.attach(part)
    return msg

@app.route("/api/providers")
def get_providers():
    return jsonify([{"name": p[0], "host": p[1], "port": p[2], "security": p[3], "logo": (p[4] if len(p) > 4 else None)} for p in PROVIDERS])

@app.route("/api/config")
def get_config():
    return jsonify(load_config())

@app.route("/api/profiles", methods=["GET"])
def get_profiles():
    cfg = load_config()
    return jsonify({"profiles": cfg.get("profiles", {}), "last_profile": cfg.get("last_profile"), "last_campaign": cfg.get("last_campaign"), "default_credentials": cfg.get("default_credentials", {})})

@app.route("/api/profiles", methods=["POST"])
def save_profile():
    data = request.json
    cfg = load_config()
    name = data.get("name", f"profile_{datetime.now().strftime('%Y%m%d%H%M%S')}")
    cfg["profiles"][name] = data.get("settings", {})
    cfg["last_profile"] = name
    save_config(cfg)
    return jsonify({"ok": True, "name": name})

@app.route("/api/profiles/<name>", methods=["PUT"])
def update_profile(name):
    data = request.json
    cfg = load_config()
    new_name = data.get("new_name", name)
    settings = data.get("settings", {})
    if new_name != name and name in cfg["profiles"]:
        del cfg["profiles"][name]
    cfg["profiles"][new_name] = settings
    cfg["last_profile"] = new_name
    save_config(cfg)
    return jsonify({"ok": True})

@app.route("/api/profiles/<name>", methods=["DELETE"])
def delete_profile(name):
    cfg = load_config()
    if name in cfg["profiles"]:
        del cfg["profiles"][name]
        if cfg.get("last_profile") == name:
            cfg["last_profile"] = None
        save_config(cfg)
    return jsonify({"ok": True})

@app.route("/api/profiles/rename", methods=["POST"])
def rename_profile():
    data = request.json
    old, new = data.get("old_name"), data.get("new_name")
    cfg = load_config()
    if old in cfg["profiles"] and new:
        cfg["profiles"][new] = cfg["profiles"].pop(old)
        if cfg.get("last_profile") == old:
            cfg["last_profile"] = new
        save_config(cfg)
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Profile not found"})

@app.route("/api/default-credentials", methods=["POST"])
def set_default_credentials():
    data = request.json
    cfg = load_config()
    cfg["default_credentials"] = {"username": data.get("username"), "password": data.get("password")}
    save_config(cfg)
    return jsonify({"ok": True})

@app.route("/api/parse-recipients", methods=["POST"])
def parse_recipients():
    mode = request.form.get("mode", "csv")
    rows = []
    if mode == "csv":
        f = request.files.get("file")
        if not f:
            return jsonify({"error": "No file"}), 400
        content = f.read().decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(content))
        for row in reader:
            rows.append({
                "FirstName": (row.get("FirstName") or "").strip(),
                "LastName": (row.get("LastName") or "").strip(),
                "Email": (row.get("Email") or "").strip()
            })
    elif mode == "paste":
        text = request.form.get("text", "")
        tokens = re.split(r"[\n\r,]+", text)
        for token in tokens:
            token = token.strip().strip('"\'<> ')
            if token and "@" in token and "." in token.split("@")[-1]:
                first, last = extract_name_from_email(token)
                rows.append({"FirstName": first, "LastName": last, "Email": token})
    elif mode == "single":
        email = request.form.get("email", "").strip()
        if email and "@" in email:
            first, last = extract_name_from_email(email)
            rows.append({"FirstName": first, "LastName": last, "Email": email})
    rows = [r for r in rows if r.get("Email")]
    deduped, dupes = deduplicate_recipients(rows)
    return jsonify({"recipients": deduped, "total": len(deduped), "dupes": dupes})

@app.route("/api/preview", methods=["POST"])
def preview_email():
    data = request.json
    template = data.get("body", DEFAULT_TEMPLATE)
    recipient = data.get("recipient", {"FirstName": "John", "LastName": "Doe", "Email": "john@example.com"})
    rendered = render(template, recipient)
    subject_rendered = render(data.get("subject", ""), recipient)
    return jsonify({"rendered": rendered, "subject": subject_rendered, "to": recipient})

@app.route("/api/campaign/start", methods=["POST"])
def start_campaign():
    global campaign_state
    if campaign_state["running"]:
        return jsonify({"error": "Campaign already running"}), 400

    data = request.json
    campaign_state = {"running": True, "log": [], "sent": 0, "failed": 0, "total": 0, "done": False, "error": None, "failed_list": []}

    def run():
        global campaign_state
        try:
            s = data
            host = s["host"]
            port = int(s["port"])
            security = s["security"]
            username = s["username"]
            password = s["password"]
            sender_name = (s.get("sender_name") or "").strip()
            sender_email = (s.get("sender_email") or "").strip()
            subject = s["subject"]
            recipients = s["recipients"]
            template = s.get("body", DEFAULT_TEMPLATE)
            subtype = s.get("email_format", "html")
            delay_range = parse_delay(s.get("delay", "1"))

            if not sender_email or "@" not in sender_email or "." not in sender_email.split("@")[-1]:
                campaign_state["error"] = "A valid Sender Email is required — recipients must see the sender identity."
                campaign_state["log"].append({"type": "error", "msg": campaign_state["error"]})
                return

            campaign_state["total"] = len(recipients)
            campaign_state["log"].append({"type": "info", "msg": f"Connecting to {host}:{port} [{security}]..."})

            server = connect(host, port, security)
            server.login(username, password)
            campaign_state["log"].append({"type": "success", "msg": f"Authenticated as {username} — sending as {sender_email}"})

            all_results = []
            stopped = False
            last_index = 0

            with server:
                for i, recipient in enumerate(recipients, 1):
                    last_index = i
                    if not campaign_state["running"]:
                        stopped = True
                        break
                    email = recipient.get("Email", "")
                    first = recipient.get("FirstName", "")
                    last = recipient.get("LastName", "")
                    rname = f"{first} {last}".strip() or email
                    ts = datetime.now().strftime("%H:%M:%S")
                    if not email:
                        campaign_state["log"].append({"type": "warn", "msg": f"[{i}/{len(recipients)}] Skipped — missing email"})
                        all_results.append({"Email": "", "FirstName": first, "LastName": last, "Status": "skipped", "Error": "missing email", "Timestamp": ts})
                        continue
                    try:
                        body = render(template, recipient)
                        msg = build_message(sender_name, sender_email, recipient, subject, body, subtype)
                        server.sendmail(sender_email, email, msg.as_string())
                        campaign_state["sent"] += 1
                        campaign_state["log"].append({"type": "sent", "msg": f"[{i}/{len(recipients)}] ✓ {rname} <{email}>"})
                        all_results.append({"Email": email, "FirstName": first, "LastName": last, "Status": "sent", "Error": "", "Timestamp": ts})
                    except Exception as e:
                        campaign_state["failed"] += 1
                        campaign_state["log"].append({"type": "fail", "msg": f"[{i}/{len(recipients)}] ✗ {rname} <{email}> — {e}"})
                        all_results.append({"Email": email, "FirstName": first, "LastName": last, "Status": "failed", "Error": str(e), "Timestamp": ts})
                        campaign_state["failed_list"].append(recipient)

                    if i < len(recipients) and campaign_state["running"]:
                        lo, hi = delay_range
                        wait = random.uniform(lo, hi) if lo != hi else lo
                        while wait > 0 and campaign_state["running"]:
                            step = min(0.2, wait)
                            time.sleep(step)
                            wait -= step

            if stopped:
                ts = datetime.now().strftime("%H:%M:%S")
                for recipient in recipients[last_index - 1:]:
                    email = recipient.get("Email", "")
                    campaign_state["log"].append({"type": "warn", "msg": f"[skipped] {email} — campaign stopped"})
                    all_results.append({"Email": email, "FirstName": recipient.get("FirstName", ""), "LastName": recipient.get("LastName", ""), "Status": "skipped", "Error": "campaign stopped", "Timestamp": ts})

            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            results_filename = f"results_{stamp}.csv"
            with open(results_filename, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=["Email", "FirstName", "LastName", "Status", "Error", "Timestamp"])
                writer.writeheader()
                writer.writerows(all_results)

            cfg = load_config()
            cfg["last_campaign"] = {"profile": s.get("profile_name", ""), "subject": subject, "csv_path": "", "sent": campaign_state["sent"], "failed": campaign_state["failed"], "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
            save_config(cfg)

            if stopped:
                campaign_state["log"].append({"type": "warn", "msg": f"Campaign stopped — {campaign_state['sent']} sent, {campaign_state['failed']} failed. Results saved to {results_filename}"})
            else:
                campaign_state["log"].append({"type": "done", "msg": f"Campaign complete — {campaign_state['sent']} sent, {campaign_state['failed']} failed. Results saved to {results_filename}"})

        except smtplib.SMTPAuthenticationError:
            campaign_state["error"] = "Authentication failed. Check your username and password/API key."
            campaign_state["log"].append({"type": "error", "msg": campaign_state["error"]})
        except Exception as e:
            campaign_state["error"] = str(e)
            campaign_state["log"].append({"type": "error", "msg": f"Connection error: {e}"})
        finally:
            campaign_state["done"] = True
            campaign_state["running"] = False

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return jsonify({"ok": True})

@app.route("/api/campaign/status")
def campaign_status():
    return jsonify(campaign_state)

@app.route("/api/campaign/stop", methods=["POST"])
def stop_campaign():
    global campaign_state
    if campaign_state.get("running"):
        campaign_state["running"] = False
        campaign_state["log"].append({"type": "warn", "msg": "Stop requested — finishing current send..."})
    return jsonify({"ok": True})

@app.route("/")
def index():
    return send_from_directory("static", "index.html")

if __name__ == "__main__":
    os.makedirs("static", exist_ok=True)
    app.run(debug=False, port=2000)
