import json, os, csv, io, re, threading, time, random, smtplib, ssl, logging
from datetime import datetime
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from flask import Flask, request, jsonify, Response, send_from_directory
from flask_cors import CORS

app = Flask(__name__, static_folder="static")
CORS(app)

CONFIG_FILE = "sender_config.json"
DEFAULT_TEMPLATE = "<p>Hi {{.FirstName}},</p>\n<p>This email is for {{.Email}}.</p>"
PROVIDERS = [
    ("Gmail",               "smtp.gmail.com",                     465, "SSL"),
    ("Gmail (STARTTLS)",    "smtp.gmail.com",                     587, "STARTTLS"),
    ("Outlook / Office365", "smtp.office365.com",                 587, "STARTTLS"),
    ("Yahoo",               "smtp.mail.yahoo.com",                465, "SSL"),
    ("Yahoo (STARTTLS)",    "smtp.mail.yahoo.com",                587, "STARTTLS"),
    ("SendGrid",            "smtp.sendgrid.net",                  587, "STARTTLS"),
    ("Mailgun",             "smtp.mailgun.org",                   587, "STARTTLS"),
    ("SMTP2GO",             "mail.smtp2go.com",                   587, "STARTTLS"),
    ("SMTP2GO (SSL)",       "mail.smtp2go.com",                   465, "SSL"),
    ("Amazon SES",          "email-smtp.us-east-1.amazonaws.com", 587, "STARTTLS"),
    ("Amazon SES (SSL)",    "email-smtp.us-east-1.amazonaws.com", 465, "SSL"),
    ("ProtonMail",          "smtp.proton.me",                     587, "STARTTLS"),
    ("Zoho",                "smtp.zoho.com",                      587, "STARTTLS"),
    ("Zoho (SSL)",          "smtp.zoho.com",                      465, "SSL"),
    ("Custom SMTP",         None,                                 None, None),
]

campaign_state = {"running": False, "log": [], "sent": 0, "failed": 0, "total": 0, "done": False, "error": None}

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

def build_message(sender_name, sender_email, recipient, subject, body, subtype, attachment_path=None):
    msg = MIMEMultipart("alternative") if not attachment_path else MIMEMultipart("mixed")
    msg["From"] = formataddr((sender_name, sender_email))
    msg["To"] = formataddr((f"{recipient.get('FirstName','')} {recipient.get('LastName','')}".strip(), recipient["Email"]))
    msg["Subject"] = subject
    text_part = MIMEText(body, subtype, "utf-8")
    if attachment_path:
        alt = MIMEMultipart("alternative")
        alt.attach(text_part)
        msg.attach(alt)
        with open(attachment_path, "rb") as f:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f'attachment; filename="{os.path.basename(attachment_path)}"')
        msg.attach(part)
    else:
        msg.attach(text_part)
    return msg

@app.route("/api/providers")
def get_providers():
    return jsonify([{"name": p[0], "host": p[1], "port": p[2], "security": p[3]} for p in PROVIDERS])

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
        content = f.read().decode("utf-8", errors="replace")
        reader = csv.DictReader(io.StringIO(content))
        for row in reader:
            rows.append({
                "FirstName": row.get("FirstName", "").strip(),
                "LastName": row.get("LastName", "").strip(),
                "Email": row.get("Email", "").strip()
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
    campaign_state = {"running": True, "log": [], "sent": 0, "failed": 0, "total": 0, "done": False, "error": None}

    def run():
        global campaign_state
        try:
            s = data
            host = s["host"]
            port = int(s["port"])
            security = s["security"]
            username = s["username"]
            password = s["password"]
            sender_name = s["sender_name"]
            sender_email = s["sender_email"]
            subject = s["subject"]
            recipients = s["recipients"]
            template = s.get("body", DEFAULT_TEMPLATE)
            subtype = s.get("email_format", "html")
            delay_range = parse_delay(s.get("delay", "1"))

            campaign_state["total"] = len(recipients)
            campaign_state["log"].append({"type": "info", "msg": f"Connecting to {host}:{port} [{security}]..."})

            server = connect(host, port, security)
            server.login(username, password)
            campaign_state["log"].append({"type": "success", "msg": f"Authenticated as {username}"})

            all_results = []
            failed_recip = []

            with server:
                for i, recipient in enumerate(recipients, 1):
                    email = recipient.get("Email", "")
                    rname = f"{recipient.get('FirstName','')} {recipient.get('LastName','')}".strip() or email
                    ts = datetime.now().strftime("%H:%M:%S")
                    if not email:
                        campaign_state["log"].append({"type": "warn", "msg": f"[{i}/{len(recipients)}] Skipped — missing email"})
                        continue
                    try:
                        body = render(template, recipient)
                        msg = build_message(sender_name, sender_email, recipient, subject, body, subtype)
                        server.sendmail(sender_email, email, msg.as_string())
                        campaign_state["sent"] += 1
                        campaign_state["log"].append({"type": "sent", "msg": f"[{i}/{len(recipients)}] ✓ {rname} <{email}>"})
                        all_results.append({"Email": email, "FirstName": recipient["FirstName"], "LastName": recipient["LastName"], "Status": "sent", "Error": "", "Timestamp": ts})
                    except Exception as e:
                        campaign_state["failed"] += 1
                        campaign_state["log"].append({"type": "fail", "msg": f"[{i}/{len(recipients)}] ✗ {rname} <{email}> — {e}"})
                        all_results.append({"Email": email, "FirstName": recipient["FirstName"], "LastName": recipient["LastName"], "Status": "failed", "Error": str(e), "Timestamp": ts})
                        failed_recip.append(recipient)

                    if i < len(recipients):
                        lo, hi = delay_range
                        time.sleep(random.uniform(lo, hi) if lo != hi else lo)

            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            results_filename = f"results_{stamp}.csv"
            with open(results_filename, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=["Email", "FirstName", "LastName", "Status", "Error", "Timestamp"])
                writer.writeheader()
                writer.writerows(all_results)

            cfg = load_config()
            cfg["last_campaign"] = {"profile": s.get("profile_name", ""), "subject": subject, "csv_path": "", "sent": campaign_state["sent"], "failed": campaign_state["failed"], "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
            save_config(cfg)

            campaign_state["log"].append({"type": "done", "msg": f"Campaign complete — {campaign_state['sent']} sent, {campaign_state['failed']} failed. Results saved to {results_filename}"})
            campaign_state["done"] = True
            campaign_state["running"] = False

        except smtplib.SMTPAuthenticationError:
            campaign_state["error"] = "Authentication failed. Check your username and password/API key."
            campaign_state["log"].append({"type": "error", "msg": campaign_state["error"]})
            campaign_state["done"] = True
            campaign_state["running"] = False
        except Exception as e:
            campaign_state["error"] = str(e)
            campaign_state["log"].append({"type": "error", "msg": f"Connection error: {e}"})
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
    campaign_state["running"] = False
    campaign_state["done"] = True
    campaign_state["log"].append({"type": "warn", "msg": "Campaign stopped by user."})
    return jsonify({"ok": True})

@app.route("/")
def index():
    return send_from_directory("static", "index.html")

if __name__ == "__main__":
    os.makedirs("static", exist_ok=True)
    app.run(debug=False, port=5000)
