# TRANSMIT — SMTP Campaign Engine

> A dark-themed, cinematic SMTP campaign tool with a full web UI, live send progress, drag-and-drop CSV upload, 15 SMTP provider support, profile management, and AI-powered email templates.

---

## GUI Dashboard

![TRANSMIT Dashboard](dashboard.png)

---

## Installation

**1. Clone the repository**

```bash
git clone https://github.com/spiderxploit/smtp_mailer.git
```

**2. Navigate into the project directory**

```bash
cd smtp_mailer
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
http://localhost:5000
```

---

## Features

- 🖤 Dark cinematic UI
- 📬 15 pre-configured SMTP provider chips
- 📁 Drag-and-drop CSV recipient upload
- 💾 Saved sender profiles with CRUD support
- 📡 Live campaign execution with real-time send feedback
- 🤖 AI-powered email template generation
- 🔗 Shared `sender_config.json` with CLI interoperability

---

## Project Structure

```
smtp_mailer/
├── smtp_mailer.py        # Main application entry point
├── sender_config.json    # Saved SMTP profiles (auto-generated)
├── templates/            # HTML templates for the web UI
└── static/               # CSS, JS, and assets
```

---

## License

MIT © [spiderxploit](https://github.com/spiderxploit)
