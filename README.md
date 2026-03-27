# 🛡️ CISSP Study App

An interactive, self-hosted web application to help you pass the **CISSP (Certified Information Systems Security Professional)** exam — now powered by **Claude AI** for personalized explanations and study planning.

Built for active practice — not passive reading. Question by question, with instant feedback, weakness tracking, AI-powered coaching, and a persistent SQLite study log.

---

## ✨ Features

- **60+ Practice Questions** across all 8 CISSP domains (expandable via Claude AI)
- **Instant Feedback** — detailed explanation + knowledge gap analysis on every wrong answer
- **🤖 Ask Claude** — on-demand deep explanation of wrong answers: why you were wrong, what to study, and a memory tip
- **🤖 AI Question Generator** — generate 5 new CISSP-quality questions per domain on demand
- **🤖 Personalized Study Plan** — Claude analyzes your weak domains and builds a weekly schedule
- **Reference Links** — clickable NIST, OWASP, and (ISC)² resources per question
- **Daily Progress Dashboard** — domain health bars, streak tracker, study calendar
- **Analytics** — domain accuracy charts, 14-day activity, weakness analysis with action plans
- **Wrong Answer Review Queue** — track and re-drill your mistakes
- **Dual Storage Mode**:
  - 🗄️ **SQLite backend** (recommended) — run `cissp_server.py`, data persists in `cissp_study.db`
  - 🌐 **Standalone mode** — open `CISSP_Study_App.html` directly, uses browser localStorage
- **Export / Import** — back up and restore your progress as JSON

---

## 📚 CISSP Domains Covered

| # | Domain | Exam Weight |
|---|--------|------------|
| 1 | Security & Risk Management | 16% |
| 2 | Asset Security | 10% |
| 3 | Security Architecture & Engineering | 13% |
| 4 | Communication & Network Security | 13% |
| 5 | Identity & Access Management (IAM) | 13% |
| 6 | Security Assessment & Testing | 12% |
| 7 | Security Operations | 13% |
| 8 | Software Development Security | 10% |

---

## 🚀 Quick Start

### Option A — With SQLite Backend (Recommended)

Persistent database that survives browser cache clears and works across all browsers.

```bash
# Clone the repo
git clone https://github.com/smaeung/cissp-study-app.git
cd cissp-study-app

# Start the local server (no dependencies — pure Python stdlib)
python3 cissp_server.py
```

Then open **http://localhost:5432** in your browser.

The server auto-opens your browser and saves all data to `cissp_study.db`.

### Option B — Standalone (No server needed)

Just open `CISSP_Study_App.html` in any browser. Progress is stored in localStorage.

> ⚠️ Use **Settings → Export** regularly to back up your data if using standalone mode.

---

## 🗂️ Project Structure

```
cissp-study-app/
├── CISSP_Study_App.html     # The full single-file web app (HTML + CSS + JS)
├── cissp_server.py          # Local Python server with SQLite REST API
├── START_CISSP_APP.command  # macOS double-click launcher
├── HOW_TO_RUN.txt           # Quick-start guide
├── .gitignore               # Excludes PDFs, .db, secrets, Office files
└── README.md                # This file
```

> **Note:** `cissp_study.db` is excluded from git (it contains your personal study data). Each user gets their own local database.

---

## 💾 Data & Privacy

- **All data stays local** — no cloud, no analytics, no accounts
- The SQLite database (`cissp_study.db`) is gitignored and never committed
- Progress export files (`cissp_progress_*.json`) are also gitignored
- Your PDF study materials are excluded from the repo (they are copyrighted (ISC)² content)

---

## 🤖 Claude AI Setup

The Claude AI features (Ask Claude, Question Generator, Study Plan) require a Claude API key.

1. Get your key at [console.anthropic.com](https://console.anthropic.com) → API Keys
2. Start the server: `python3 cissp_server.py`
3. Open **Settings** → **Claude AI Integration**
4. Paste your key (starts with `sk-ant-`) and click **Save Key**

Your API key is stored locally in `cissp_config.json` (gitignored — never committed).

> **Cost:** Claude API usage is billed per token. Each "Ask Claude" call costs roughly $0.001–0.003 USD.

---

## 🔧 Server API Reference

When running `cissp_server.py`, the following endpoints are available:

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Serves the web app |
| GET | `/api/ping` | Health check + DB path |
| GET | `/api/state` | Full study state (stats, wrong answers, activity) |
| POST | `/api/answer` | Record a quiz answer |
| POST | `/api/import` | Restore from JSON backup |
| POST | `/api/clear-wrong` | Clear wrong answer history |
| GET | `/api/history` | Raw answer history |
| GET | `/api/config` | Check if Claude API key is configured |
| POST | `/api/config` | Save Claude API key |
| POST | `/api/claude/explain` | Get Claude's deep explanation for a wrong answer |
| POST | `/api/claude/generate-questions` | Generate new CISSP questions for a domain |
| POST | `/api/claude/study-plan` | Generate personalized study plan |

---

## 📈 Passing Score Target

The CISSP exam requires a scaled score of **700 out of 1000**. Target **75%+** accuracy per domain in this app before your exam date.

---

## 📖 Study Resources

- [NIST Cybersecurity Framework](https://www.nist.gov/cyberframework)
- [NIST SP 800-53 Security Controls](https://csrc.nist.gov/publications/detail/sp/800-53/rev-5/final)
- [OWASP Top 10](https://owasp.org/Top10/)
- [NIST SP 800-61 Incident Response](https://csrc.nist.gov/publications/detail/sp/800-61/rev-2/final)
- [(ISC)² Official CISSP Page](https://www.isc2.org/Certifications/CISSP)

---

## 📄 License

MIT License — free to use, modify, and share.

Questions are based on publicly available CISSP curriculum concepts and NIST/OWASP frameworks.
