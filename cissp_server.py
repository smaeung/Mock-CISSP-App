#!/usr/bin/env python3
"""
CISSP Study App — Local SQLite Backend Server
Run this with: python3 cissp_server.py
Then open http://localhost:5432 in your browser.
All study data is saved to cissp_study.db in the same folder.
"""

import sqlite3
import json
import os
import sys
import threading
import urllib.request
import urllib.error
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timedelta
import pathlib

PORT = 5432
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cissp_study.db")
HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "CISSP_Study_App.html")
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cissp_config.json")

CLAUDE_MODEL = "claude-sonnet-4-6"
CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"

# ─── Config (API Key) ─────────────────────────────────────────────────────────

def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_config(config):
    with open(CONFIG_PATH, 'w') as f:
        json.dump(config, f, indent=2)

# ─── Claude API ───────────────────────────────────────────────────────────────

def call_claude(api_key, messages, system_prompt="", max_tokens=1500):
    """Call Claude API using only stdlib urllib — no pip dependencies needed."""
    payload = {
        "model": CLAUDE_MODEL,
        "max_tokens": max_tokens,
        "messages": messages
    }
    if system_prompt:
        payload["system"] = system_prompt

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        CLAUDE_API_URL,
        data=data,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=45) as resp:
        return json.loads(resp.read())

# ─── Database Setup ──────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS answers (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            q_id        INTEGER NOT NULL,
            domain_id   INTEGER NOT NULL,
            is_correct  INTEGER NOT NULL,
            answered_at TEXT NOT NULL DEFAULT (date('now'))
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS wrong_answers (
            q_id        INTEGER PRIMARY KEY,
            domain_id   INTEGER NOT NULL,
            question_text TEXT,
            count       INTEGER NOT NULL DEFAULT 1,
            last_wrong  TEXT NOT NULL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS study_sessions (
            session_date    TEXT PRIMARY KEY,
            questions_done  INTEGER DEFAULT 0,
            correct         INTEGER DEFAULT 0
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS meta (
            key     TEXT PRIMARY KEY,
            value   TEXT
        )
    """)

    conn.commit()
    conn.close()
    print(f"  ✅ Database ready: {DB_PATH}")

# ─── API Logic ────────────────────────────────────────────────────────────────

def get_full_state():
    conn = get_db()
    c = conn.cursor()

    # Totals
    c.execute("SELECT COUNT(*) as total, SUM(is_correct) as correct FROM answers")
    row = c.fetchone()
    total = row["total"] or 0
    correct = int(row["correct"] or 0)

    # Per-domain stats
    c.execute("""
        SELECT domain_id, COUNT(*) as answered, SUM(is_correct) as correct
        FROM answers GROUP BY domain_id
    """)
    domain_stats = {}
    for r in c.fetchall():
        domain_stats[str(r["domain_id"])] = {
            "answered": r["answered"],
            "correct": int(r["correct"] or 0)
        }

    # Wrong answers
    c.execute("SELECT * FROM wrong_answers ORDER BY count DESC")
    wrong = []
    for r in c.fetchall():
        wrong.append({
            "qId": r["q_id"],
            "domainId": r["domain_id"],
            "questionText": r["question_text"],
            "count": r["count"],
            "lastWrong": r["last_wrong"]
        })

    # Study days
    c.execute("SELECT session_date FROM study_sessions ORDER BY session_date")
    study_days = [r["session_date"] for r in c.fetchall()]

    # Daily activity (last 30 days)
    c.execute("""
        SELECT session_date as d, questions_done as answered, correct
        FROM study_sessions
        ORDER BY session_date DESC LIMIT 30
    """)
    daily_activity = {}
    for r in c.fetchall():
        daily_activity[r["d"]] = {"answered": r["answered"], "correct": r["correct"]}

    # Streak
    streak = 0
    today = datetime.now().date()
    check_date = today
    while check_date.isoformat() in study_days or str(check_date) in study_days:
        streak += 1
        check_date -= timedelta(days=1)

    # Meta
    c.execute("SELECT value FROM meta WHERE key='last_study_date'")
    row = c.fetchone()
    last_study_date = row["value"] if row else None

    conn.close()
    return {
        "totalAnswered": total,
        "totalCorrect": correct,
        "domainStats": domain_stats,
        "wrongAnswers": wrong,
        "studyDays": study_days,
        "dailyActivity": daily_activity,
        "streakDays": streak,
        "lastStudyDate": last_study_date
    }

def record_answer(q_id, domain_id, is_correct, question_text):
    today = datetime.now().date().isoformat()
    conn = get_db()
    c = conn.cursor()

    # Record answer
    c.execute("INSERT INTO answers (q_id, domain_id, is_correct, answered_at) VALUES (?,?,?,?)",
              (q_id, domain_id, 1 if is_correct else 0, today))

    # Update or insert session
    c.execute("""
        INSERT INTO study_sessions (session_date, questions_done, correct)
        VALUES (?, 1, ?)
        ON CONFLICT(session_date) DO UPDATE SET
            questions_done = questions_done + 1,
            correct = correct + ?
    """, (today, 1 if is_correct else 0, 1 if is_correct else 0))

    # Track wrong answers
    if not is_correct:
        c.execute("""
            INSERT INTO wrong_answers (q_id, domain_id, question_text, count, last_wrong)
            VALUES (?, ?, ?, 1, ?)
            ON CONFLICT(q_id) DO UPDATE SET
                count = count + 1,
                last_wrong = ?
        """, (q_id, domain_id, question_text[:120] if question_text else "", today, today))

    # Update meta
    c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('last_study_date', ?)", (today,))

    conn.commit()
    conn.close()
    return {"ok": True}

def import_state(data):
    """Import state from localStorage JSON export."""
    conn = get_db()
    c = conn.cursor()

    # Clear existing data
    c.execute("DELETE FROM answers")
    c.execute("DELETE FROM wrong_answers")
    c.execute("DELETE FROM study_sessions")
    c.execute("DELETE FROM meta")

    # Re-insert study sessions from dailyActivity
    for date, activity in data.get("dailyActivity", {}).items():
        c.execute("""
            INSERT OR REPLACE INTO study_sessions (session_date, questions_done, correct)
            VALUES (?, ?, ?)
        """, (date, activity.get("answered", 0), activity.get("correct", 0)))

    # Re-insert wrong answers
    for w in data.get("wrongAnswers", []):
        c.execute("""
            INSERT OR REPLACE INTO wrong_answers (q_id, domain_id, question_text, count, last_wrong)
            VALUES (?, ?, ?, ?, ?)
        """, (w.get("qId"), w.get("domainId"), w.get("questionText", ""), w.get("count", 1), w.get("lastWrong", "")))

    # Restore meta
    if data.get("lastStudyDate"):
        c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('last_study_date', ?)", (data["lastStudyDate"],))

    conn.commit()
    conn.close()
    return {"ok": True, "imported": True}

def clear_wrong_answers():
    conn = get_db()
    conn.execute("DELETE FROM wrong_answers")
    conn.commit()
    conn.close()
    return {"ok": True}

def get_history(domain_id=None, limit=200):
    conn = get_db()
    c = conn.cursor()
    if domain_id:
        c.execute("SELECT * FROM answers WHERE domain_id=? ORDER BY id DESC LIMIT ?", (domain_id, limit))
    else:
        c.execute("SELECT * FROM answers ORDER BY id DESC LIMIT ?", (limit,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows

# ─── Claude Handlers ──────────────────────────────────────────────────────────

def handle_claude_explain(body):
    config = load_config()
    api_key = config.get("apiKey", "")
    if not api_key:
        return {"error": "No API key configured. Add your Claude API key in Settings."}, 400

    question = body.get("question", "")
    options = body.get("options", [])
    correct_answer = body.get("correctAnswer", "")
    user_answer = body.get("userAnswer", "")
    domain = body.get("domain", "")
    existing_explanation = body.get("existingExplanation", "")

    system_prompt = (
        "You are an expert CISSP exam coach with deep knowledge of all 8 CISSP domains. "
        "Your role is to help candidates understand why they got a question wrong and exactly "
        "what CISSP concepts they need to study to master this topic. "
        "Be concise, exam-focused, and practical. Do not pad your response."
    )

    user_message = f"""CISSP Question from Domain: {domain}

Question: {question}

Answer choices:
{chr(10).join(f"{chr(65+i)}. {opt}" for i, opt in enumerate(options))}

The student selected: {user_answer}
Correct answer: {correct_answer}

Static explanation from study guide: {existing_explanation}

Please provide:
1. WHY the student's answer is wrong (specific misconception to avoid)
2. WHY the correct answer is right (core CISSP concept it tests)
3. STUDY FOCUS: The specific CISSP concepts, frameworks, or standards to review (be specific — name the NIST SP, ISO standard, or (ISC)2 concept)
4. MEMORY TIP: One memorable phrase or analogy to remember this for the exam

Format your response as JSON with keys: "whyWrong", "whyCorrect", "studyFocus", "memoryTip"
Return ONLY valid JSON, no markdown code blocks."""

    try:
        resp = call_claude(api_key, [{"role": "user", "content": user_message}], system_prompt, max_tokens=800)
        text = resp["content"][0]["text"].strip()
        # Try to parse as JSON
        try:
            parsed = json.loads(text)
            return parsed, 200
        except json.JSONDecodeError:
            # Return raw text if JSON parsing fails
            return {"whyWrong": text, "whyCorrect": "", "studyFocus": "", "memoryTip": ""}, 200
    except urllib.error.HTTPError as e:
        body_err = e.read().decode()
        return {"error": f"Claude API error: {e.code} — {body_err}"}, 502
    except Exception as e:
        return {"error": str(e)}, 500


def handle_claude_generate_questions(body):
    config = load_config()
    api_key = config.get("apiKey", "")
    if not api_key:
        return {"error": "No API key configured."}, 400

    domain_id = body.get("domain", 1)
    domain_name = body.get("domainName", "")
    count = min(int(body.get("count", 5)), 10)  # cap at 10 per call
    existing_ids = body.get("existingIds", [])
    max_existing_id = max(existing_ids) if existing_ids else 100

    system_prompt = (
        "You are a CISSP exam question writer with expertise in all 8 CISSP domains. "
        "Generate realistic, exam-quality multiple-choice questions that test deep understanding, "
        "not just memorization. Questions should reflect the scenario-based style of the actual CISSP exam. "
        "Return ONLY valid JSON — no markdown, no explanation outside the JSON."
    )

    user_message = f"""Generate {count} CISSP exam-quality multiple-choice questions for:
Domain {domain_id}: {domain_name}

Requirements:
- Each question must have exactly 4 answer choices (A, B, C, D)
- Questions should be scenario-based and test application of knowledge
- Vary difficulty: mix conceptual and scenario-based questions
- Cover different subtopics within the domain
- Include common misconceptions as wrong choices

Return a JSON array of objects with this EXACT schema:
[
  {{
    "id": {max_existing_id + 1},
    "domain": {domain_id},
    "text": "The full question text",
    "options": ["Option A text", "Option B text", "Option C text", "Option D text"],
    "answer": 0,
    "explanation": "Detailed explanation of why the correct answer is right and others are wrong",
    "weakness": "The common knowledge gap or misconception this question targets",
    "refs": [{{"text": "Reference name", "url": "https://csrc.nist.gov or other authoritative URL"}}]
  }}
]

Use sequential IDs starting from {max_existing_id + 1}.
Return ONLY the JSON array."""

    try:
        resp = call_claude(api_key, [{"role": "user", "content": user_message}], system_prompt, max_tokens=2500)
        text = resp["content"][0]["text"].strip()
        # Strip markdown code fences if present
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        try:
            questions = json.loads(text)
            if not isinstance(questions, list):
                questions = [questions]
            return {"questions": questions}, 200
        except json.JSONDecodeError as e:
            return {"error": f"Failed to parse generated questions: {str(e)}", "raw": text[:500]}, 500
    except urllib.error.HTTPError as e:
        body_err = e.read().decode()
        return {"error": f"Claude API error: {e.code} — {body_err}"}, 502
    except Exception as e:
        return {"error": str(e)}, 500


def handle_claude_study_plan(body):
    config = load_config()
    api_key = config.get("apiKey", "")
    if not api_key:
        return {"error": "No API key configured."}, 400

    domain_stats = body.get("domainStats", {})
    target_date = body.get("targetDate", "")
    domain_names = {
        "1": "Security & Risk Management",
        "2": "Asset Security",
        "3": "Security Architecture & Engineering",
        "4": "Communication & Network Security",
        "5": "Identity & Access Management (IAM)",
        "6": "Security Assessment & Testing",
        "7": "Security Operations",
        "8": "Software Development Security"
    }
    domain_weights = {
        "1": "16%", "2": "10%", "3": "13%", "4": "13%",
        "5": "13%", "6": "12%", "7": "13%", "8": "10%"
    }

    # Build performance summary
    perf_lines = []
    for did, stats in domain_stats.items():
        answered = stats.get("answered", 0)
        correct = stats.get("correct", 0)
        pct = round(correct / answered * 100) if answered > 0 else None
        name = domain_names.get(did, f"Domain {did}")
        weight = domain_weights.get(did, "?")
        if pct is not None:
            perf_lines.append(f"  Domain {did} ({name}, {weight} of exam): {pct}% accuracy ({correct}/{answered} questions)")
        else:
            perf_lines.append(f"  Domain {did} ({name}, {weight} of exam): Not yet practiced")

    system_prompt = (
        "You are a CISSP exam coach specializing in personalized study planning. "
        "Create practical, actionable study plans based on the student's current performance data. "
        "Focus on high-impact areas first — domains with low accuracy AND high exam weight. "
        "The passing score is 700/1000 (approximately 70% scaled). Target 75%+ per domain."
    )

    target_info = f"Target exam date: {target_date}" if target_date else "No specific exam date set"

    user_message = f"""Create a personalized CISSP study plan based on this student's performance:

{chr(10).join(perf_lines)}

{target_info}
CISSP passing score: 700/1000 (approximately 70% scaled, targeting 75%+ per domain)

Create a structured study plan with:
1. Overall assessment (2-3 sentences on current readiness)
2. Priority domains to study (ranked by urgency: low accuracy + high exam weight = highest priority)
3. Weekly schedule (specific daily focus areas)
4. Practice recommendations (what types of questions to focus on)
5. Exam-day readiness checklist

Return as JSON with keys:
{{
  "readinessAssessment": "2-3 sentence overall assessment",
  "priorityOrder": ["Domain X: reason", ...],
  "weeklyPlan": [
    {{"week": 1, "focus": "Domain X & Y", "dailyGoal": "20 questions/day", "topics": ["topic1", "topic2"]}},
    ...
  ],
  "practiceRecommendations": ["recommendation 1", ...],
  "examReadinessChecklist": ["item 1", ...]
}}

Return ONLY valid JSON."""

    try:
        resp = call_claude(api_key, [{"role": "user", "content": user_message}], system_prompt, max_tokens=2000)
        text = resp["content"][0]["text"].strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        try:
            plan = json.loads(text)
            return plan, 200
        except json.JSONDecodeError:
            return {"readinessAssessment": text, "priorityOrder": [], "weeklyPlan": [], "practiceRecommendations": [], "examReadinessChecklist": []}, 200
    except urllib.error.HTTPError as e:
        body_err = e.read().decode()
        return {"error": f"Claude API error: {e.code} — {body_err}"}, 502
    except Exception as e:
        return {"error": str(e)}, 500

# ─── HTTP Handler ─────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        # Quiet logging — only show errors
        if "404" in str(args) or "500" in str(args):
            print(f"  ⚠️  {args}")

    def send_json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def send_cors_preflight(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_OPTIONS(self):
        self.send_cors_preflight()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        # Serve the HTML app
        if path == "/" or path == "/index.html":
            try:
                with open(HTML_PATH, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", len(content))
                self.end_headers()
                self.wfile.write(content)
            except FileNotFoundError:
                self.send_json({"error": "HTML file not found"}, 404)
            return

        # API routes
        if path == "/api/state":
            self.send_json(get_full_state())
        elif path == "/api/history":
            domain_id = int(qs["domain"][0]) if "domain" in qs else None
            self.send_json(get_history(domain_id))
        elif path == "/api/ping":
            self.send_json({"ok": True, "db": DB_PATH, "version": "3.0"})
        elif path == "/api/config":
            config = load_config()
            self.send_json({"hasApiKey": bool(config.get("apiKey", ""))})
        else:
            self.send_json({"error": "Not found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        # Read body
        length = int(self.headers.get("Content-Length", 0))
        body = {}
        if length > 0:
            try:
                body = json.loads(self.rfile.read(length))
            except Exception:
                self.send_json({"error": "Invalid JSON"}, 400)
                return

        if path == "/api/answer":
            result = record_answer(
                body.get("qId"),
                body.get("domainId"),
                body.get("isCorrect", False),
                body.get("questionText", "")
            )
            self.send_json(result)

        elif path == "/api/import":
            result = import_state(body)
            self.send_json(result)

        elif path == "/api/clear-wrong":
            result = clear_wrong_answers()
            self.send_json(result)

        elif path == "/api/config":
            api_key = body.get("apiKey", "").strip()
            if not api_key.startswith("sk-ant-"):
                self.send_json({"error": "Invalid API key format. Key must start with 'sk-ant-'"}, 400)
                return
            config = load_config()
            config["apiKey"] = api_key
            save_config(config)
            self.send_json({"ok": True})

        elif path == "/api/claude/explain":
            result, status = handle_claude_explain(body)
            self.send_json(result, status)

        elif path == "/api/claude/generate-questions":
            result, status = handle_claude_generate_questions(body)
            self.send_json(result, status)

        elif path == "/api/claude/study-plan":
            result, status = handle_claude_study_plan(body)
            self.send_json(result, status)

        else:
            self.send_json({"error": "Not found"}, 404)

# ─── Main ─────────────────────────────────────────────────────────────────────

def open_browser():
    import time, webbrowser
    time.sleep(1.5)
    webbrowser.open(f"http://localhost:{PORT}")

if __name__ == "__main__":
    print("\n" + "="*55)
    print("  🛡️  CISSP Study App — Local Server")
    print("="*55)
    init_db()
    print(f"  🌐 Server: http://localhost:{PORT}")
    print(f"  📂 Folder: {os.path.dirname(os.path.abspath(__file__))}")
    print(f"  💾 Database: cissp_study.db")
    config = load_config()
    if config.get("apiKey"):
        print(f"  🤖 Claude AI: Configured ✅")
    else:
        print(f"  🤖 Claude AI: Not configured (add key in Settings)")
    print(f"\n  Open http://localhost:{PORT} in your browser.")
    print("  Press Ctrl+C to stop.\n")

    # Open browser automatically
    threading.Thread(target=open_browser, daemon=True).start()

    try:
        server = HTTPServer(("localhost", PORT), Handler)
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  👋 Server stopped.")
        sys.exit(0)
