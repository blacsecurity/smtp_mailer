import json, os, csv, io, re, threading, time, random, smtplib, ssl, socket, logging, html as html_lib
from collections import deque
from concurrent.futures import ThreadPoolExecutor
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

MAX_WORKERS = 32
LOG_MAXLEN = 1000
FAILED_MAXLEN = 5000
RESULTS_DIR = "results"

_state_lock = threading.Lock()
_campaign_counter = {"n": 0}
campaign_state = {
    "id": 0, "running": False, "done": False, "error": None, "stopped": False,
    "sent": 0, "failed": 0, "skipped": 0, "total": 0,
    "started_at": None, "finished_at": None,
    "log": deque(maxlen=LOG_MAXLEN),
    "seq": 0,
    "failed_list": deque(maxlen=FAILED_MAXLEN),
    "results_file": None,
}

class FatalAuth(Exception):
    """Raised when SMTP authentication fails and the campaign must abort."""

def _log(kind, msg):
    with _state_lock:
        campaign_state["seq"] += 1
        campaign_state["log"].append({
            "seq": campaign_state["seq"], "type": kind, "msg": msg,
            "t": datetime.now().strftime("%H:%M:%S"),
        })

def _bump(field, n=1):
    with _state_lock:
        campaign_state[field] += n

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

def clean_header(value):
    """Strip CR/LF so user input cannot inject extra headers."""
    return re.sub(r"[\r\n]+", " ", str(value or "")).strip()

def build_message(sender_name, sender_email, recipient, subject, body, subtype, attachment_path=None, unsubscribe_url=None):
    domain = sender_email.split("@")[-1] if "@" in sender_email else None
    if attachment_path:
        msg = MIMEMultipart("mixed")
        container = MIMEMultipart("alternative")
    else:
        msg = MIMEMultipart("alternative")
        container = msg

    sender_name = clean_header(sender_name)
    subject = clean_header(subject)
    to_name = clean_header(f"{recipient.get('FirstName','')} {recipient.get('LastName','')}".strip())
    msg["From"] = formataddr((sender_name, sender_email))
    msg["To"] = formataddr((to_name, recipient["Email"]))
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=domain)
    if sender_email:
        msg["Reply-To"] = formataddr((sender_name, sender_email))
        msg["Sender"] = formataddr((sender_name, sender_email))
    if unsubscribe_url:
        # Gmail/Outlook give inbox placement credit to one-click unsubscribe.
        msg["List-Unsubscribe"] = f"<{clean_header(unsubscribe_url)}>"
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"

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

class _Claim:
    """Thread-safe work counter shared by all send workers."""
    def __init__(self, total):
        self.total = total
        self.next_i = 0
        self.lock = threading.Lock()

    def take(self):
        with self.lock:
            if self.next_i >= self.total:
                return None
            i = self.next_i
            self.next_i += 1
            return i


class SMTPWorker:
    """One SMTP connection that reconnects transparently if it drops."""
    def __init__(self, host, port, security, username, password):
        self.host, self.port, self.security = host, port, security
        self.username, self.password = username, password
        self.server = None

    def connect(self):
        server = connect(self.host, self.port, self.security)
        try:
            server.login(self.username, self.password)
        except Exception:
            try:
                server.close()
            except Exception:
                pass
            raise
        self.server = server

    def send(self, sender, recipient, raw):
        if self.server is None:
            self.connect()
        self.server.sendmail(sender, recipient, raw)

    def reset(self):
        try:
            if self.server:
                self.server.close()
        except Exception:
            pass
        self.server = None

    def close(self):
        try:
            if self.server:
                self.server.quit()
        except Exception:
            self.reset()
        self.server = None


def _is_transient(exc):
    """True for connection-level errors and 4xx SMTP replies worth retrying."""
    if isinstance(exc, (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError,
                        smtplib.SMTPHeloError, ssl.SSLError, ConnectionError, socket.timeout)):
        return True
    if isinstance(exc, smtplib.SMTPResponseException):
        code = getattr(exc, "smtp_code", 0) or 0
        return 400 <= code < 500
    return isinstance(exc, OSError) and not isinstance(exc, smtplib.SMTPException)


def deliver(worker, sender_email, recipient_email, raw, retries):
    """Send one message, reconnecting and retrying transient failures."""
    attempt = 0
    while True:
        try:
            worker.send(sender_email, recipient_email, raw)
            return True, ""
        except smtplib.SMTPAuthenticationError as e:
            raise FatalAuth(str(e))
        except Exception as e:
            if not _is_transient(e) or attempt >= retries:
                return False, str(e)
            attempt += 1
            time.sleep(min(2 ** attempt, 15))
            worker.reset()


def _open_results():
    os.makedirs(RESULTS_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(RESULTS_DIR, f"results_{stamp}.csv")
    fh = open(path, "w", newline="", encoding="utf-8")
    writer = csv.DictWriter(fh, fieldnames=["Email", "FirstName", "LastName", "Status", "Error", "Timestamp"])
    writer.writeheader()
    fh.flush()
    return path, fh, writer


def run_campaign(s):
    try:
        host = s["host"]
        port = int(s["port"])
        security = s.get("security", "STARTTLS")
        username = s.get("username", "")
        password = s.get("password", "")
        sender_name = (s.get("sender_name") or "").strip()
        sender_email = (s.get("sender_email") or "").strip()
        subject = s.get("subject", "")
        recipients = s.get("recipients", [])
        template = s.get("body", DEFAULT_TEMPLATE)
        subtype = s.get("email_format", "html")
        delay_range = parse_delay(s.get("delay", "1"))
        unsubscribe_url = (s.get("unsubscribe_url") or "").strip() or None

        try:
            workers = int(s.get("workers", 1) or 1)
        except (TypeError, ValueError):
            workers = 1
        workers = max(1, min(MAX_WORKERS, workers))

        try:
            retries = int(s.get("retries", 1) or 0)
        except (TypeError, ValueError):
            retries = 1
        retries = max(0, min(5, retries))

        if not sender_email or "@" not in sender_email or "." not in sender_email.split("@")[-1]:
            campaign_state["error"] = "A valid Sender Email is required — recipients must see the sender identity."
            _log("error", campaign_state["error"])
            return

        total = len(recipients)
        with _state_lock:
            campaign_state["total"] = total
            campaign_state["started_at"] = time.time()

        _log("info", f"Connecting to {host}:{port} [{security}] · {workers} worker(s) · {retries} retr{'y' if retries == 1 else 'ies'}")

        # Fail fast on connection/authentication problems before spawning workers.
        try:
            probe = connect(host, port, security)
        except Exception as e:
            campaign_state["error"] = f"Connection error: {e}"
            _log("error", campaign_state["error"])
            return
        try:
            probe.login(username, password)
        except smtplib.SMTPAuthenticationError:
            campaign_state["error"] = "Authentication failed. Check your username and password/API key."
            _log("error", campaign_state["error"])
            try:
                probe.close()
            except Exception:
                pass
            return
        finally:
            try:
                probe.quit()
            except Exception:
                try:
                    probe.close()
                except Exception:
                    pass

        _log("success", f"Authenticated — sending as {sender_email}")

        results_file, fh, writer = _open_results()
        with _state_lock:
            campaign_state["results_file"] = results_file

        processed = bytearray(total)
        claim = _Claim(total)
        rlock = threading.Lock()
        write_count = [0]

        def record(row):
            with rlock:
                writer.writerow(row)
                write_count[0] += 1
                if write_count[0] % 200 == 0:
                    fh.flush()

        def worker(wid):
            w = SMTPWorker(host, port, security, username, password)
            try:
                w.connect()
            except smtplib.SMTPAuthenticationError:
                campaign_state["error"] = "Authentication failed. Check your username and password/API key."
                with _state_lock:
                    campaign_state["running"] = False
                _log("error", campaign_state["error"])
                return
            except Exception as e:
                _log("warn", f"[w{wid}] connection failed — {e}")
                return
            try:
                while campaign_state["running"]:
                    i = claim.take()
                    if i is None:
                        break
                    recipient = recipients[i]
                    email = (recipient.get("Email") or "").strip()
                    first = recipient.get("FirstName", "")
                    last = recipient.get("LastName", "")
                    ts = datetime.now().strftime("%H:%M:%S")
                    if not email:
                        processed[i] = 1
                        _bump("skipped")
                        record({"Email": "", "FirstName": first, "LastName": last, "Status": "skipped", "Error": "missing email", "Timestamp": ts})
                        continue
                    rname = f"{first} {last}".strip() or email
                    try:
                        body = render(template, recipient)
                        msg = build_message(sender_name, sender_email, recipient, subject, body, subtype, unsubscribe_url=unsubscribe_url)
                        ok, err = deliver(w, sender_email, email, msg.as_string(), retries)
                    except FatalAuth:
                        campaign_state["error"] = "Authentication failed. Check your username and password/API key."
                        with _state_lock:
                            campaign_state["running"] = False
                        _log("error", campaign_state["error"])
                        return
                    except Exception as e:
                        ok, err = False, str(e)
                    processed[i] = 1
                    if ok:
                        _bump("sent")
                        _log("sent", f"[{i + 1}/{total}] {rname} <{email}>")
                        record({"Email": email, "FirstName": first, "LastName": last, "Status": "sent", "Error": "", "Timestamp": ts})
                    else:
                        _bump("failed")
                        _log("fail", f"[{i + 1}/{total}] {rname} <{email}> — {err}")
                        with _state_lock:
                            campaign_state["failed_list"].append(recipient)
                        record({"Email": email, "FirstName": first, "LastName": last, "Status": "failed", "Error": str(err), "Timestamp": ts})
                    lo, hi = delay_range
                    wait = random.uniform(lo, hi) if lo != hi else lo
                    while wait > 0 and campaign_state["running"]:
                        step = min(0.2, wait)
                        time.sleep(step)
                        wait -= step
            finally:
                w.close()

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(worker, wid) for wid in range(1, workers + 1)]
            for f in futures:
                try:
                    f.result()
                except Exception:
                    pass

        unprocessed = [i for i in range(total) if not processed[i]]
        stopped = campaign_state["error"] is None and len(unprocessed) > 0
        with _state_lock:
            campaign_state["stopped"] = stopped
        for i in unprocessed:
            recipient = recipients[i]
            processed[i] = 1
            _bump("skipped")
            record({"Email": recipient.get("Email", ""), "FirstName": recipient.get("FirstName", ""), "LastName": recipient.get("LastName", ""), "Status": "skipped", "Error": "not sent", "Timestamp": datetime.now().strftime("%H:%M:%S")})
        fh.flush()
        fh.close()

        cfg = load_config()
        cfg["last_campaign"] = {
            "profile": s.get("profile_name", ""), "subject": subject, "csv_path": results_file,
            "sent": campaign_state["sent"], "failed": campaign_state["failed"],
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        save_config(cfg)

        if campaign_state["error"]:
            pass
        elif stopped:
            _log("warn", f"Campaign stopped — {campaign_state['sent']} sent, {campaign_state['failed']} failed, {campaign_state['skipped']} skipped. Results saved to {results_file}")
        else:
            _log("done", f"Campaign complete — {campaign_state['sent']} sent, {campaign_state['failed']} failed, {campaign_state['skipped']} skipped. Results saved to {results_file}")

    except smtplib.SMTPAuthenticationError:
        campaign_state["error"] = "Authentication failed. Check your username and password/API key."
        _log("error", campaign_state["error"])
    except Exception as e:
        campaign_state["error"] = str(e)
        _log("error", f"Campaign error: {e}")
    finally:
        with _state_lock:
            campaign_state["finished_at"] = time.time()
            campaign_state["done"] = True
            campaign_state["running"] = False


@app.route("/api/campaign/start", methods=["POST"])
def start_campaign():
    global campaign_state
    if campaign_state["running"]:
        return jsonify({"error": "Campaign already running"}), 400

    data = request.json or {}
    with _state_lock:
        _campaign_counter["n"] += 1
        campaign_state = {
            "id": _campaign_counter["n"],
            "running": True, "done": False, "error": None, "stopped": False,
            "sent": 0, "failed": 0, "skipped": 0, "total": 0,
            "started_at": None, "finished_at": None,
            "log": deque(maxlen=LOG_MAXLEN),
            "seq": 0, "failed_list": deque(maxlen=FAILED_MAXLEN),
            "results_file": None,
        }

    threading.Thread(target=run_campaign, args=(data,), daemon=True).start()
    return jsonify({"ok": True})


@app.route("/api/campaign/status")
def campaign_status():
    try:
        since = int(request.args.get("since", 0) or 0)
    except (TypeError, ValueError):
        since = 0
    with _state_lock:
        new_log = [e for e in campaign_state["log"] if e["seq"] > since]
        processed = campaign_state["sent"] + campaign_state["failed"] + campaign_state["skipped"]
        started = campaign_state["started_at"]
        finished = campaign_state["finished_at"]
        elapsed = ((finished or time.time()) - started) if started else 0
        rate = (processed / elapsed) if elapsed > 0 else 0
        remaining = max(0, campaign_state["total"] - processed)
        eta = (remaining / rate) if rate > 0 else None
        failed_list = list(campaign_state["failed_list"])
        return jsonify({
            "id": campaign_state["id"],
            "running": campaign_state["running"],
            "done": campaign_state["done"],
            "error": campaign_state["error"],
            "stopped": campaign_state["stopped"],
            "sent": campaign_state["sent"],
            "failed": campaign_state["failed"],
            "skipped": campaign_state["skipped"],
            "total": campaign_state["total"],
            "seq": campaign_state["seq"],
            "log": new_log,
            "failed_list": failed_list,
            "failed_truncated": campaign_state["failed"] > len(failed_list),
            "processed": processed,
            "rate": round(rate, 2),
            "elapsed": round(elapsed, 1),
            "eta": (round(eta) if eta is not None else None),
            "results_file": campaign_state["results_file"],
        })


@app.route("/api/campaign/stop", methods=["POST"])
def stop_campaign():
    if campaign_state.get("running"):
        with _state_lock:
            campaign_state["running"] = False
        _log("warn", "Stop requested — workers finishing current sends...")
    return jsonify({"ok": True})

@app.route("/")
def index():
    return send_from_directory("static", "index.html")

@app.route("/sw.js")
def service_worker():
    return send_from_directory("static", "sw.js", mimetype="application/javascript")

if __name__ == "__main__":
    os.makedirs("static", exist_ok=True)
    os.makedirs(RESULTS_DIR, exist_ok=True)
    app.run(debug=False, port=2000)
