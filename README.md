# 🛡️ CISSP Study App

An interactive, self-hosted web application to help you pass the **CISSP (Certified Information Systems Security Professional)** exam — powered by **Claude AI** for personalized explanations and study planning.

Built for active practice — not passive reading. Question by question, with instant feedback, weakness tracking, AI coaching, and a persistent SQLite study log.

---

## ✨ Features

- **60+ Practice Questions** across all 8 CISSP domains (expandable via Claude AI)
- **Instant Feedback** — explanation + knowledge gap analysis on every wrong answer
- **🤖 Ask Claude** — deep explanation of wrong answers: why you were wrong, what to study, memory tip
- **🤖 AI Question Generator** — generate 5 new CISSP-quality questions per domain on demand
- **🤖 Personalized Study Plan** — Claude analyzes your weak domains and builds a weekly schedule
- **Reference Links** — clickable NIST, OWASP, and (ISC)² resources per question
- **Daily Dashboard** — domain health bars, study streak, 30-day calendar
- **Analytics** — accuracy charts, 14-day activity, weakness analysis with action plans
- **Wrong Answer Review Queue** — track and re-drill your mistakes
- **Export / Import** — back up and restore progress as JSON

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

## 🚀 How to Run

### Prerequisites

- **Python 3** (any version 3.6+) — no pip packages needed, uses only stdlib
- A modern web browser (Chrome, Firefox, Safari, Edge)

Check you have Python 3:
```bash
python3 --version
```

---

### Option A — With SQLite Backend (Recommended)

Persistent database that survives browser cache clears and works across all browsers on your machine.

**Step 1 — Clone or download the project:**
```bash
git clone https://github.com/YOUR_USERNAME/Mock-CISSP-App.git
cd Mock-CISSP-App
```

**Step 2 — Start the local server:**
```bash
python3 cissp_server.py
```

**Step 3 — Open your browser:**

The server auto-opens your browser. If not, go to:
```
http://localhost:5432
```

Your study data is saved to `cissp_study.db` in the project folder.

To stop the server: press **Ctrl+C** in Terminal.

**macOS shortcut:** double-click `START_CISSP_APP.command` — it starts the server and opens the browser automatically.

---

### Option B — Standalone (No server needed)

Open `CISSP_Study_App.html` directly in any browser (double-click the file).

Progress is stored in browser localStorage — simpler to start, but data is lost if you clear your browser cache.

> Use **Settings → Export** regularly to back up your data when using this mode.

---

### Side-by-Side Comparison

| | Option A (Server) | Option B (Standalone) |
|--|--|--|
| Setup | `python3 cissp_server.py` | Open HTML file |
| Data storage | SQLite database file | Browser localStorage |
| Survives cache clear | ✅ Yes | ❌ No |
| Claude AI features | ✅ Yes | ❌ No |
| Works offline | ✅ Yes | ✅ Yes |
| Recommended | ⭐ Yes | Quick start only |

---

## 🤖 Claude AI Setup

Claude AI features (Ask Claude, Question Generator, Study Plan) require a Claude API key and the server running (Option A).

**Step 1 — Get a Claude API key:**
- Go to [console.anthropic.com](https://console.anthropic.com) → API Keys → Create key
- Copy the key (starts with `sk-ant-`)

**Step 2 — Add the key in the app:**
- Open the app at `http://localhost:5432`
- Go to **Settings** → **Claude AI Integration**
- Paste your key and click **Save Key**

Your key is stored in `cissp_config.json` locally — it is gitignored and never committed.

**What Claude AI enables:**

| Feature | Where to find it |
|---------|-----------------|
| Ask Claude (wrong answer explanation) | Appears after answering incorrectly during a quiz |
| Generate 5 More Questions | Domain Guide page → per-domain button |
| Personalized Study Plan | Analytics page → Study Plan Generator card |

> **Cost estimate:** Each "Ask Claude" call costs approximately $0.001–0.003 USD.

---

## 🗂️ Project Structure

```
Mock-CISSP-App/
├── CISSP_Study_App.html     # Full single-file web app (HTML + CSS + JS)
├── cissp_server.py          # Local Python server with SQLite REST API + Claude proxy
├── START_CISSP_APP.command  # macOS double-click launcher
├── HOW_TO_RUN.txt           # Quick-start guide (plain text)
├── .gitignore               # Excludes DB, API key config, exports
└── README.md                # This file

# Generated at runtime (gitignored):
├── cissp_study.db           # SQLite database (your study data)
└── cissp_config.json        # Claude API key (never committed)
```

---

## 🔧 Server API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Serves the web app |
| GET | `/api/ping` | Health check + DB path |
| GET | `/api/state` | Full study state (stats, wrong answers, activity) |
| POST | `/api/answer` | Record a quiz answer |
| POST | `/api/import` | Restore from JSON backup |
| POST | `/api/clear-wrong` | Clear wrong answer history |
| GET | `/api/history` | Raw answer history (optional `?domain=N`) |
| GET | `/api/config` | Check if Claude API key is configured |
| POST | `/api/config` | Save Claude API key |
| POST | `/api/claude/explain` | Claude explanation for a wrong answer |
| POST | `/api/claude/generate-questions` | Generate new questions for a domain |
| POST | `/api/claude/study-plan` | Generate personalized study plan |

---

## 💾 Data & Privacy

- **All data stays local** — no cloud, no analytics, no accounts
- SQLite database (`cissp_study.db`) is gitignored — your personal data stays on your machine
- Claude API key (`cissp_config.json`) is gitignored — never committed
- Progress exports (`cissp_progress_*.json`) are gitignored
- The only external connection is to the Anthropic API when you explicitly use Claude features

---

## 📈 Passing Score Target

The CISSP exam uses a scaled score system — passing is **700 out of 1000**.

Target **75%+ accuracy per domain** in this app before your exam date. Use the Study Plan Generator to prioritize domains by exam weight × your current accuracy.

---

## 📖 Study Resources

- [NIST Cybersecurity Framework](https://www.nist.gov/cyberframework)
- [NIST SP 800-53 Security Controls](https://csrc.nist.gov/publications/detail/sp/800-53/rev-5/final)
- [NIST SP 800-30 Risk Assessment](https://csrc.nist.gov/publications/detail/sp/800-30/rev-1/final)
- [OWASP Top 10](https://owasp.org/Top10/)
- [NIST SP 800-61 Incident Response](https://csrc.nist.gov/publications/detail/sp/800-61/rev-2/final)
- [(ISC)² Official CISSP Page](https://www.isc2.org/Certifications/CISSP)

---

## 📄 License

MIT License — free to use, modify, and share.

Questions are based on publicly available CISSP curriculum concepts and NIST/OWASP frameworks.
