# TRANSMIT — SMTP Campaign Engine

> A dark-themed, cinematic SMTP campaign tool with a full web UI, live send progress, drag-and-drop CSV upload, 20+ SMTP provider support, profile management, and AI-powered email templates.

---

## GUI Dashboard

![TRANSMIT Dashboard](dashboard.png)

---

## Installation

**1. Clone the repository**

```bash
git clone https://github.com/blacsecurity/smtp_mailer.git
```

**2. Navigate into the project directory**

```bash
cd smtp_mailer
```

**3. Set up a virtual environment and install dependencies**

**Linux**

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

**macOS**

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

**Windows (PowerShell)**

```powershell
python -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

---

## Usage

**macOS / Linux**

```bash
python3 smtp_mailer.py
```

**Windows**

```bash
python smtp_mailer.py
```

Once running, open your browser and navigate to:

```
http://localhost:2000
```

---

## Features

- 🖤 Dark cinematic UI with a collapsible sidebar
- 📬 20+ pre-configured SMTP provider chips with official brand logos
- 📁 Drag-and-drop CSV recipient upload
- 💾 Saved sender profiles with create, edit, delete, and clear-all support
- 📡 Live campaign execution with real-time send feedback and stop control
- 🚀 Scalable engine — parallel worker pool (up to 32 connections), automatic reconnect and per-email retries, built for 50,000+ recipient campaigns
- 🧷 Bounded live feed with paginated status updates and incremental `results/` CSV writes (constant memory)
- ✉️ Multipart plain-text + HTML emails with `Date`, `Message-ID`, `Reply-To` and `List-Unsubscribe` headers
- 🔔 Browser push notifications for campaign start, completion and errors
- 🎭 Sender identity masking (From / Sender / Reply-To)
- 🤖 AI-powered email template generation
- 🔗 Shared `sender_config.json` with CLI interoperability

---

## Project Structure

```
smtp_mailer/
├── smtp_mailer.py        # Main application entry point
├── requirements.txt      # Python dependencies
├── README.md             # Project documentation
├── dashboard.png         # Dashboard screenshot
├── sender_config.json    # Saved SMTP profiles (auto-generated)
├── results/              # Per-campaign delivery CSVs (auto-generated)
└── static/
    ├── index.html        # Single-page web UI (HTML, CSS, JS)
    ├── sw.js             # Service worker for push notifications
    └── smtpmailer.png    # App logo / asset
```

---

## Scaling to Large Campaigns

The sending engine is built for high-volume lists:

- **Workers** — open several SMTP connections in parallel (`1`–`32`). Each worker keeps its
  own connection and reconnects automatically if the server drops it.
- **Retries** — transient failures (connection resets and `4xx` replies) are retried with
  exponential backoff; permanent `5xx` rejections are not retried.
- **Bounded memory** — the live feed keeps only the most recent 1000 entries and the UI pulls
  new entries incrementally, so a 50,000-recipient run does not accumulate in RAM.
- **Incremental results** — every result is appended to `results/results_<timestamp>.csv` as it
  happens, not held in memory until the end.

For a 50k campaign, start with a low worker count and a small delay, then increase while watching
your provider's rate limits. Bulk providers (Amazon SES, SendGrid, Mailgun, …) accept far more
parallelism than mailbox providers such as Gmail or Outlook.

---

## Deliverability

Technical best practices are already applied — `Date`, `Message-ID`, `Reply-To`, `Sender`,
`List-Unsubscribe` + `List-Unsubscribe-Post`, a plain-text alternative and an envelope sender that
matches your From address. **Inbox placement itself is decided by the receiving provider** and
depends mainly on authentication you must configure at your DNS provider:

- **SPF** — authorise your sending host/provider.
- **DKIM** — sign the mail with a key whose public half is published at your domain.
- **DMARC** — align SPF/DKIM and tell receivers how to treat failures.

Send only to opted-in contacts, keep bounces low and use a consistent, warm sending domain.

---

## License

MIT © [blacsecurity](https://github.com/blacsecurity)
