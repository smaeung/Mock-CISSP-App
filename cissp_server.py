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
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "cissp_study.db")
HTML_PATH = os.path.join(BASE_DIR, "CISSP_Study_App.html")
CONFIG_PATH = os.path.join(BASE_DIR, "cissp_config.json")
QUESTIONS_DIR = os.path.join(BASE_DIR, "questions")

CLAUDE_MODEL = "claude-sonnet-4-6"
CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"

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

# ─── Question File Loading ────────────────────────────────────────────────────

_question_cache = None

def load_question_files():
    """Load all domain question JSON files from questions/ directory."""
    global _question_cache
    if _question_cache is not None:
        return _question_cache
    questions = []
    if os.path.isdir(QUESTIONS_DIR):
        for fname in sorted(os.listdir(QUESTIONS_DIR)):
            if fname.endswith(".json"):
                try:
                    with open(os.path.join(QUESTIONS_DIR, fname)) as f:
                        data = json.load(f)
                        if isinstance(data, list):
                            questions.extend(data)
                except Exception as e:
                    print(f"  ⚠️  Could not load {fname}: {e}")
    _question_cache = questions
    return questions

# ─── Multi-LLM API ───────────────────────────────────────────────────────────

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
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())["content"][0]["text"].strip()

def call_gemini(api_key, messages, system_prompt="", model="gemini-1.5-flash", max_tokens=1500):
    """Call Google Gemini API."""
    contents = []
    if system_prompt:
        contents.append({"role": "user", "parts": [{"text": system_prompt}]})
        contents.append({"role": "model", "parts": [{"text": "Understood."}]})
    for m in messages:
        role = "model" if m["role"] == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": m["content"]}]})
    payload = {
        "contents": contents,
        "generationConfig": {"maxOutputTokens": max_tokens}
    }
    url = GEMINI_API_URL.format(model=model, key=api_key)
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data,
        headers={"content-type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read())
        return result["candidates"][0]["content"]["parts"][0]["text"].strip()

def call_ollama(base_url, model, messages, system_prompt="", max_tokens=1500):
    """Call local Ollama API."""
    all_messages = []
    if system_prompt:
        all_messages.append({"role": "system", "content": system_prompt})
    all_messages.extend(messages)
    payload = {"model": model, "messages": all_messages, "stream": False,
               "options": {"num_predict": max_tokens}}
    url = base_url.rstrip("/") + "/api/chat"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data,
        headers={"content-type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read())["message"]["content"].strip()

def call_openai_compat(api_key, base_url, model, messages, system_prompt="", max_tokens=1500):
    """Call OpenAI-compatible API (OpenAI, MiniMax, Qwen/DashScope)."""
    all_messages = []
    if system_prompt:
        all_messages.append({"role": "system", "content": system_prompt})
    all_messages.extend(messages)
    payload = {"model": model, "messages": all_messages, "max_tokens": max_tokens}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(base_url.rstrip("/") + "/chat/completions",
        data=data,
        headers={"Authorization": f"Bearer {api_key}", "content-type": "application/json"},
        method="POST")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())["choices"][0]["message"]["content"].strip()

def call_llm(messages, system_prompt="", max_tokens=1500):
    """Route to the configured LLM provider. Returns (text, error)."""
    config = load_config()
    provider = config.get("provider", "claude")
    providers = config.get("providers", {})
    pc = providers.get(provider, {})

    try:
        if provider == "claude":
            api_key = pc.get("apiKey") or config.get("apiKey", "")
            if not api_key:
                return None, "No Claude API key configured. Add it in Settings."
            text = call_claude(api_key, messages, system_prompt, max_tokens)
        elif provider == "gemini":
            api_key = pc.get("apiKey", "")
            if not api_key:
                return None, "No Gemini API key configured."
            model = pc.get("model", "gemini-1.5-flash")
            text = call_gemini(api_key, messages, system_prompt, model, max_tokens)
        elif provider == "ollama":
            base_url = pc.get("baseUrl", "http://localhost:11434")
            model = pc.get("model", "llama3")
            text = call_ollama(base_url, model, messages, system_prompt, max_tokens)
        elif provider == "openai":
            api_key = pc.get("apiKey", "")
            if not api_key:
                return None, "No OpenAI API key configured."
            model = pc.get("model", "gpt-4o-mini")
            text = call_openai_compat(api_key, "https://api.openai.com/v1", model, messages, system_prompt, max_tokens)
        elif provider == "minimax":
            api_key = pc.get("apiKey", "")
            if not api_key:
                return None, "No MiniMax API key configured."
            model = pc.get("model", "abab6.5s-chat")
            text = call_openai_compat(api_key, "https://api.minimax.chat/v1", model, messages, system_prompt, max_tokens)
        elif provider == "qwen":
            api_key = pc.get("apiKey", "")
            if not api_key:
                return None, "No Qwen API key configured."
            model = pc.get("model", "qwen-turbo")
            text = call_openai_compat(api_key, "https://dashscope.aliyuncs.com/compatible-mode/v1", model, messages, system_prompt, max_tokens)
        else:
            return None, f"Unknown provider: {provider}"
        return text, None
    except urllib.error.HTTPError as e:
        body_err = e.read().decode()[:300]
        return None, f"API error {e.code}: {body_err}"
    except Exception as e:
        return None, str(e)

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

    c.execute("""
        CREATE TABLE IF NOT EXISTS claude_cache (
            q_id        INTEGER NOT NULL,
            is_correct  INTEGER NOT NULL,
            response    TEXT NOT NULL,
            created_at  TEXT NOT NULL,
            PRIMARY KEY (q_id, is_correct)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS mock_exams (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at      TEXT NOT NULL,
            completed_at    TEXT,
            duration_limit  INTEGER DEFAULT 240,
            question_ids    TEXT NOT NULL,
            answers         TEXT DEFAULT '{}',
            domain_results  TEXT DEFAULT '{}',
            total_questions INTEGER DEFAULT 0,
            correct_count   INTEGER DEFAULT 0,
            score           REAL,
            status          TEXT DEFAULT 'in_progress'
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

# ─── Claude Cache ─────────────────────────────────────────────────────────────

def get_claude_cache(q_id, is_correct):
    """Return cached Claude response dict, or None if not cached."""
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT response FROM claude_cache WHERE q_id=? AND is_correct=?",
              (q_id, 1 if is_correct else 0))
    row = c.fetchone()
    conn.close()
    if row:
        try:
            return json.loads(row["response"])
        except Exception:
            return None
    return None

def set_claude_cache(q_id, is_correct, response_dict):
    """Store a Claude response dict in the cache."""
    conn = get_db()
    today = datetime.now().date().isoformat()
    conn.execute("""
        INSERT OR REPLACE INTO claude_cache (q_id, is_correct, response, created_at)
        VALUES (?, ?, ?, ?)
    """, (q_id, 1 if is_correct else 0, json.dumps(response_dict), today))
    conn.commit()
    conn.close()

# ─── Claude Handlers ──────────────────────────────────────────────────────────

def _parse_llm_json(text):
    """Strip markdown code fences and parse JSON from LLM output.
    Tries multiple strategies to handle varied LLM output formats."""
    import re
    text = text.strip()

    # Strategy 1: Strip ALL forms of code fences using regex
    # Handles: ```json {...}```, ```json\n{...}\n```, ```{...}```, json {...}
    fence_stripped = re.sub(r'^```[a-zA-Z]*\s*', '', text)   # remove opening fence + lang tag
    fence_stripped = re.sub(r'\s*```$', '', fence_stripped)    # remove closing fence
    fence_stripped = fence_stripped.strip()

    # Strategy 2: Direct parse of fence-stripped text
    try:
        return json.loads(fence_stripped)
    except (json.JSONDecodeError, ValueError):
        pass

    # Strategy 3: Direct parse of original text (in case it was already clean JSON)
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass

    # Strategy 4: Extract first {...} JSON object via greedy regex (handles preamble/postamble text)
    match = re.search(r'\{[\s\S]*\}', fence_stripped or text)
    if match:
        try:
            return json.loads(match.group(0))
        except (json.JSONDecodeError, ValueError):
            pass

    # Strategy 5: Extract first [...] JSON array via regex
    match = re.search(r'\[[\s\S]*\]', fence_stripped or text)
    if match:
        try:
            return json.loads(match.group(0))
        except (json.JSONDecodeError, ValueError):
            pass

    raise ValueError(f"Could not parse JSON. Preview: {text[:300]}")

def handle_claude_explain(body):
    q_id = body.get("qId")
    question = body.get("question", "")
    options = body.get("options", [])
    correct_answer = body.get("correctAnswer", "")
    user_answer = body.get("userAnswer", "")
    is_correct = body.get("isCorrect", False)
    domain = body.get("domain", "")
    existing_explanation = body.get("existingExplanation", "")

    # Check SQLite cache first
    if q_id is not None:
        cached = get_claude_cache(q_id, is_correct)
        if cached:
            cached["_cached"] = True
            return cached, 200

    system_prompt = (
        "You are an expert CISSP exam coach with deep knowledge of all 8 CISSP domains. "
        "Help candidates deeply understand every question — whether they got it right or wrong. "
        "Be concise, exam-focused, and practical. Do not pad your response."
    )

    options_text = chr(10).join(f"{chr(65+i)}. {opt}" for i, opt in enumerate(options))

    if is_correct:
        user_message = f"""CISSP Question from Domain: {domain}

Question: {question}

Answer choices:
{options_text}

The student selected the CORRECT answer: {correct_answer}
Study guide explanation: {existing_explanation}

Give them a deeper understanding. Return ONLY this JSON (all values must be plain strings):
{{"whyCorrect": "...", "whyWrong": "...", "studyFocus": "...", "memoryTip": "..."}}"""
    else:
        user_message = f"""CISSP Question from Domain: {domain}

Question: {question}

Answer choices:
{options_text}

The student selected: {user_answer}
Correct answer: {correct_answer}
Study guide explanation: {existing_explanation}

Return ONLY this JSON (all values must be plain strings):
{{"whyWrong": "...", "whyCorrect": "...", "studyFocus": "...", "memoryTip": "..."}}"""

    text, error = call_llm([{"role": "user", "content": user_message}], system_prompt, max_tokens=900)
    if error:
        return {"error": error}, 502

    try:
        parsed = _parse_llm_json(text)
    except Exception:
        parsed = {"whyCorrect": text, "whyWrong": "", "studyFocus": "", "memoryTip": ""} if is_correct \
            else {"whyWrong": text, "whyCorrect": "", "studyFocus": "", "memoryTip": ""}

    if q_id is not None:
        set_claude_cache(q_id, is_correct, parsed)

    return parsed, 200


def handle_claude_generate_questions(body):
    domain_id = body.get("domain", 1)
    domain_name = body.get("domainName", "")
    count = min(int(body.get("count", 5)), 10)
    existing_ids = body.get("existingIds", [])
    max_existing_id = max(existing_ids) if existing_ids else 100

    system_prompt = (
        "You are a CISSP exam question writer with expertise in all 8 CISSP domains. "
        "Generate realistic, exam-quality multiple-choice questions that test deep understanding. "
        "Return ONLY valid JSON — no markdown, no explanation outside the JSON."
    )

    user_message = f"""Generate {count} CISSP scenario-based questions for Domain {domain_id}: {domain_name}.

Return a JSON array:
[
  {{
    "id": {max_existing_id + 1},
    "domain": {domain_id},
    "text": "question text",
    "options": ["A text", "B text", "C text", "D text"],
    "answer": 0,
    "explanation": "why correct and why others are wrong",
    "weakness": "knowledge gap this tests"
  }}
]
Use sequential IDs from {max_existing_id + 1}. Return ONLY the JSON array."""

    text, error = call_llm([{"role": "user", "content": user_message}], system_prompt, max_tokens=2500)
    if error:
        return {"error": error}, 502

    try:
        questions = _parse_llm_json(text)
        if not isinstance(questions, list):
            questions = [questions]
        return {"questions": questions}, 200
    except Exception as e:
        return {"error": f"Failed to parse questions: {str(e)}", "raw": text[:500]}, 500


def handle_claude_study_plan(body):
    domain_stats = body.get("domainStats", {})
    target_date = body.get("targetDate", "")
    domain_names = {
        "1": "Security & Risk Management", "2": "Asset Security",
        "3": "Security Architecture & Engineering", "4": "Communication & Network Security",
        "5": "Identity & Access Management (IAM)", "6": "Security Assessment & Testing",
        "7": "Security Operations", "8": "Software Development Security"
    }
    domain_weights = {
        "1": "16%", "2": "10%", "3": "13%", "4": "13%",
        "5": "13%", "6": "12%", "7": "13%", "8": "10%"
    }

    perf_lines = []
    for did, stats in domain_stats.items():
        answered = stats.get("answered", 0)
        correct = stats.get("correct", 0)
        pct = round(correct / answered * 100) if answered > 0 else None
        name = domain_names.get(did, f"Domain {did}")
        weight = domain_weights.get(did, "?")
        if pct is not None:
            perf_lines.append(f"  D{did} ({name}, {weight}): {pct}% ({correct}/{answered})")
        else:
            perf_lines.append(f"  D{did} ({name}, {weight}): Not practiced")

    system_prompt = (
        "You are a CISSP exam coach specializing in personalized study planning. "
        "Create practical, actionable study plans based on student performance data. "
        "The passing score is 700/1000. Target 75%+ per domain. "
        "CRITICAL: Respond with ONLY a raw JSON object. "
        "Do NOT use markdown code fences (no ```). Do NOT add any text before or after the JSON."
    )

    target_info = f"Target exam date: {target_date}" if target_date else "No specific exam date"

    user_message = f"""CISSP student performance:
{chr(10).join(perf_lines)}
{target_info}

Respond with ONLY this raw JSON (no code fences, no extra text):
{{
  "readinessAssessment": "2-3 sentence assessment of readiness",
  "priorityOrder": ["Domain X: reason for priority"],
  "weeklyPlan": [{{"week": 1, "focus": "topic", "dailyGoal": "daily target", "topics": ["topic1", "topic2"]}}],
  "practiceRecommendations": ["recommendation 1"],
  "examReadinessChecklist": ["checklist item 1"]
}}"""

    text, error = call_llm([{"role": "user", "content": user_message}], system_prompt, max_tokens=2500)
    if error:
        return {"error": error}, 502

    try:
        return _parse_llm_json(text), 200
    except Exception:
        # Last resort: try brute-force regex extraction directly on the raw LLM output
        import re
        match = re.search(r'\{[\s\S]*\}', text)
        if match:
            try:
                return json.loads(match.group(0)), 200
            except Exception:
                pass
        # Truly unrecoverable — return error so the UI shows a helpful message
        return {"error": f"AI returned an unreadable response. Please try again. (Parse failed on: {text[:120]}...)"},  502


# ─── Mock Exam ────────────────────────────────────────────────────────────────

def start_mock_exam(body):
    """Start a new mock exam session with 125 questions sampled across domains."""
    import random
    all_questions = load_question_files()
    num_questions = int(body.get("numQuestions", 125))
    duration = int(body.get("durationMinutes", 240))

    if not all_questions:
        return {"error": "No question files found. Ensure questions/ directory exists."}, 400

    # Group by domain for balanced sampling
    by_domain = {}
    for q in all_questions:
        d = str(q.get("domain", 1))
        by_domain.setdefault(d, []).append(q["id"])

    # Weight sampling by exam domain weights
    weights = {"1":16,"2":10,"3":13,"4":13,"5":13,"6":12,"7":13,"8":10}
    selected_ids = []
    total_weight = sum(weights.values())
    for did, w in weights.items():
        pool = by_domain.get(did, [])
        if pool:
            n = max(1, round(num_questions * w / total_weight))
            selected_ids.extend(random.sample(pool, min(n, len(pool))))

    # Top up to target if needed
    random.shuffle(selected_ids)
    selected_ids = selected_ids[:num_questions]

    now = datetime.now().isoformat()
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO mock_exams (started_at, duration_limit, question_ids, total_questions, status)
        VALUES (?, ?, ?, ?, 'in_progress')
    """, (now, duration, json.dumps(selected_ids), len(selected_ids)))
    exam_id = cur.lastrowid
    conn.commit()
    conn.close()

    return {"examId": exam_id, "questionIds": selected_ids,
            "totalQuestions": len(selected_ids), "durationMinutes": duration}, 200


def submit_mock_exam(body):
    """Submit final answers for a mock exam and compute results."""
    exam_id = body.get("examId")
    answers = body.get("answers", {})  # {q_id: selected_idx}

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM mock_exams WHERE id=?", (exam_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return {"error": "Exam not found"}, 404
    if row["status"] != "in_progress":
        conn.close()
        return {"error": "Exam already completed"}, 400

    # Load questions to check answers
    all_questions = load_question_files()
    q_map = {str(q["id"]): q for q in all_questions}
    question_ids = json.loads(row["question_ids"])

    correct_count = 0
    domain_results = {}
    answer_details = {}

    for qid in question_ids:
        q = q_map.get(str(qid))
        if not q:
            continue
        did = str(q.get("domain", 1))
        selected = answers.get(str(qid))
        is_correct = selected is not None and int(selected) == int(q["answer"])
        if is_correct:
            correct_count += 1
        domain_results.setdefault(did, {"answered": 0, "correct": 0})
        domain_results[did]["answered"] += 1
        if is_correct:
            domain_results[did]["correct"] += 1
        answer_details[str(qid)] = {
            "selected": selected,
            "correct": q["answer"],
            "isCorrect": is_correct
        }

    total = len(question_ids)
    score = round(correct_count / total * 100, 1) if total > 0 else 0
    now = datetime.now().isoformat()

    cur.execute("""
        UPDATE mock_exams SET
            completed_at=?, answers=?, domain_results=?,
            correct_count=?, score=?, status='completed'
        WHERE id=?
    """, (now, json.dumps(answer_details), json.dumps(domain_results),
          correct_count, score, exam_id))
    conn.commit()
    conn.close()

    return {
        "examId": exam_id,
        "score": score,
        "correctCount": correct_count,
        "totalQuestions": total,
        "domainResults": domain_results,
        "answerDetails": answer_details,
        "passed": score >= 70
    }, 200


def get_mock_exam_history():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, started_at, completed_at, total_questions, correct_count, score, status, duration_limit
        FROM mock_exams ORDER BY id DESC LIMIT 5
    """)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return {"exams": rows}, 200


def get_mock_exam_detail(exam_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM mock_exams WHERE id=?", (exam_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return {"error": "Not found"}, 404
    d = dict(row)
    for field in ("question_ids", "answers", "domain_results"):
        try:
            d[field] = json.loads(d[field] or "null")
        except Exception:
            pass
    return d, 200

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
            qs_count = len(load_question_files())
            self.send_json({"ok": True, "db": DB_PATH, "version": "4.0",
                           "questionBankSize": qs_count})
        elif path == "/api/config":
            config = load_config()
            provider = config.get("provider", "claude")
            providers = config.get("providers", {})
            # Return which providers have keys configured (not the keys themselves)
            configured = {}
            for p, pc in providers.items():
                has_key = bool(pc.get("apiKey") or pc.get("baseUrl"))
                configured[p] = has_key
            # Legacy: claude key at root level
            if config.get("apiKey"):
                configured["claude"] = True
            self.send_json({"hasApiKey": bool(config.get("apiKey", "") or
                           (providers.get("claude", {}).get("apiKey"))),
                           "provider": provider, "configuredProviders": configured})
        elif path == "/api/questions":
            domain_filter = int(qs["domain"][0]) if "domain" in qs else None
            questions = load_question_files()
            if domain_filter:
                questions = [q for q in questions if q.get("domain") == domain_filter]
            self.send_json({"questions": questions, "total": len(questions)})
        elif path == "/api/mock-exam/history":
            result, status = get_mock_exam_history()
            self.send_json(result, status)
        elif path.startswith("/api/mock-exam/") and path.count("/") == 3:
            try:
                exam_id = int(path.split("/")[-1])
                result, status = get_mock_exam_detail(exam_id)
                self.send_json(result, status)
            except ValueError:
                self.send_json({"error": "Invalid exam ID"}, 400)
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
            config = load_config()
            # Support both legacy {"apiKey": ...} and new {"provider": ..., "providers": {...}}
            if "apiKey" in body:
                api_key = body.get("apiKey", "").strip()
                if not api_key.startswith("sk-ant-"):
                    self.send_json({"error": "Invalid API key format. Must start with 'sk-ant-'"}, 400)
                    return
                config["apiKey"] = api_key
                config.setdefault("provider", "claude")
                config.setdefault("providers", {})
                config["providers"]["claude"] = {"apiKey": api_key}
            if "provider" in body:
                config["provider"] = body["provider"]
            if "providerConfig" in body:
                # {"provider": "gemini", "config": {"apiKey": "...", "model": "..."}}
                pc = body["providerConfig"]
                pname = body.get("provider", config.get("provider", "claude"))
                config.setdefault("providers", {})[pname] = pc
            save_config(config)
            self.send_json({"ok": True})

        elif path == "/api/mock-exam/start":
            result, status = start_mock_exam(body)
            self.send_json(result, status)

        elif path == "/api/mock-exam/submit":
            result, status = submit_mock_exam(body)
            self.send_json(result, status)

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
    q_count = len(load_question_files())
    print(f"  🌐 Server: http://localhost:{PORT}")
    print(f"  📂 Folder: {BASE_DIR}")
    print(f"  💾 Database: cissp_study.db")
    print(f"  📝 Question bank: {q_count} advanced questions loaded")
    config = load_config()
    provider = config.get("provider", "claude")
    has_key = bool(config.get("apiKey") or config.get("providers", {}).get(provider, {}).get("apiKey") or
                   config.get("providers", {}).get(provider, {}).get("baseUrl"))
    if has_key:
        print(f"  🤖 AI Provider: {provider} ✅")
    else:
        print(f"  🤖 AI Provider: not configured (add key in Settings)")
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
