#!/usr/bin/env python3
"""
cissp_server.py — Local Backend Server for CISSP Study App
===========================================================
Author      : Sungho Maeung
AI Pair     : Claude Sonnet 4.6 (Anthropic)
Purpose     : Serve the single-page HTML frontend, proxy AI provider APIs,
              and persist all study data locally in SQLite.

Why Python stdlib only?
  - No `pip install` needed — runs on any machine with Python 3.8+
  - Reduces setup friction for CISSP candidates who may not be developers
  - urllib.request, sqlite3, json, and http.server cover everything we need

Usage:
  python3 cissp_server.py
  Then open http://localhost:5432 in your browser.
"""

# ── Standard library imports (no pip dependencies required) ──────────────────
import sqlite3        # Built-in SQL database — stores all study progress locally
import json           # Serialize/deserialize API payloads and config files
import os             # File path operations, directory checks
import sys            # sys.exit() for clean Ctrl+C shutdown
import threading      # Launch browser-open in background so server starts first
import urllib.request # HTTP client for calling external AI provider APIs
import urllib.error   # Catch HTTP errors from AI provider API calls
from http.server import HTTPServer, BaseHTTPRequestHandler  # Built-in web server
from urllib.parse import urlparse, parse_qs  # Parse URL path and query string
from datetime import datetime, timedelta     # Dates for streak calculation and timestamps
import pathlib        # Cross-platform path utilities (imported for future use)

# ── Server configuration constants ───────────────────────────────────────────
PORT = 5432          # Localhost port — 5432 is memorable (also PostgreSQL's default)
                     # Change this if another process already uses 5432

# Build absolute paths relative to this script's location so the app works
# from any working directory (e.g., double-clicking START_CISSP_APP.command)
BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
DB_PATH      = os.path.join(BASE_DIR, "cissp_study.db")       # SQLite database
HTML_PATH    = os.path.join(BASE_DIR, "CISSP_Study_App.html") # Frontend SPA
CONFIG_PATH  = os.path.join(BASE_DIR, "cissp_config.json")    # API keys (gitignored)
QUESTIONS_DIR = os.path.join(BASE_DIR, "questions")           # JSON question files

# ── AI provider API constants ─────────────────────────────────────────────────
CLAUDE_MODEL   = "claude-sonnet-4-6"   # Anthropic's balanced model — good speed + quality
CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"

# Gemini URL uses format() placeholders filled in call_gemini()
# {model} = gemini-2.0-flash etc., {key} = user's API key
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"


# ═══════════════════════════════════════════════════════════════════════════════
# CONFIG MANAGEMENT
# API keys are stored in cissp_config.json (gitignored — never committed).
# The server reads config on every AI call so key changes take effect immediately
# without restarting the server.
# ═══════════════════════════════════════════════════════════════════════════════

def load_config():
    """
    Read cissp_config.json and return it as a dict.
    Returns {} (empty dict) if file doesn't exist or is malformed —
    this keeps the app functional even before any API key is configured.
    """
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as f:
                return json.load(f)
        except Exception:
            pass  # Corrupt config treated as empty — user can reconfigure
    return {}

def save_config(config):
    """
    Write the config dict back to cissp_config.json with pretty-printing.
    indent=2 keeps the file human-readable so users can inspect/edit it manually.
    """
    with open(CONFIG_PATH, 'w') as f:
        json.dump(config, f, indent=2)


# ═══════════════════════════════════════════════════════════════════════════════
# QUESTION FILE LOADING
# Questions are split across 8 JSON files (one per CISSP domain) in questions/.
# The cache prevents re-reading files on every API request — files only load once
# per server session, which keeps /api/questions fast.
# ═══════════════════════════════════════════════════════════════════════════════

_question_cache = None   # Module-level cache; None = not yet loaded, [] = loaded but empty

def load_question_files():
    """
    Load all *.json files from the questions/ directory and merge into one list.
    Uses an in-memory cache so files are only read from disk once per server run.

    Why sorted()? Ensures deterministic load order (domain1 before domain2 etc.)
    so question IDs are consistent across restarts.

    Returns: list of question dicts, each with keys:
      id, domain, text, options (list[4]), answer (0-3), explanation, weakness, references
    """
    global _question_cache
    # Return cached result if already loaded — avoids redundant disk I/O
    if _question_cache is not None:
        return _question_cache

    questions = []
    if os.path.isdir(QUESTIONS_DIR):
        # sorted() gives stable alphabetical order: domain1_*.json → domain8_*.json
        for fname in sorted(os.listdir(QUESTIONS_DIR)):
            if fname.endswith(".json"):
                try:
                    with open(os.path.join(QUESTIONS_DIR, fname)) as f:
                        data = json.load(f)
                        # Each file should be a JSON array; skip files that aren't
                        if isinstance(data, list):
                            questions.extend(data)
                except Exception as e:
                    # Log warning but continue — one bad file shouldn't crash the server
                    print(f"  ⚠️  Could not load {fname}: {e}")

    _question_cache = questions
    return questions


# ═══════════════════════════════════════════════════════════════════════════════
# MULTI-LLM API LAYER
# Each function below handles one provider's specific API format.
# Why separate functions instead of one generic call?
#   - Each provider has a different HTTP endpoint, auth header, and JSON structure
#   - Keeping them separate makes it easy to add or update providers independently
#   - call_llm() is the single entry point that reads config and routes to the right one
# ═══════════════════════════════════════════════════════════════════════════════

def call_claude(api_key, messages, system_prompt="", max_tokens=1500):
    """
    Call Anthropic's Claude API using only stdlib urllib (no requests library needed).

    Why urllib instead of the `anthropic` SDK?
      - Zero additional dependencies — no pip install
      - The SDK adds ~50MB and requires pip, which breaks our "no setup" philosophy

    Request format: Anthropic uses a custom messages API with:
      - x-api-key header (not Bearer token)
      - anthropic-version header (required for API stability)
      - system prompt as a top-level field (not inside messages array)

    Returns: plain text string from the model
    """
    payload = {
        "model": CLAUDE_MODEL,
        "max_tokens": max_tokens,
        "messages": messages   # [{"role": "user", "content": "..."}, ...]
    }
    if system_prompt:
        payload["system"] = system_prompt   # Claude uses top-level "system" field

    data = json.dumps(payload).encode("utf-8")   # Must be bytes for urllib
    req = urllib.request.Request(
        CLAUDE_API_URL,
        data=data,
        headers={
            "x-api-key": api_key,                    # Anthropic auth (not "Authorization: Bearer")
            "anthropic-version": "2023-06-01",       # Required header — pins the API contract
            "content-type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        # Response structure: {"content": [{"type": "text", "text": "..."}], ...}
        return json.loads(resp.read())["content"][0]["text"].strip()


def call_gemini(api_key, messages, system_prompt="", model="gemini-1.5-flash", max_tokens=1500):
    """
    Call Google Gemini API.

    Why inject system_prompt as a fake user/model turn?
      - Gemini does not have a dedicated system field like Claude does
      - Prepending it as a user message + model "Understood." reply is the
        recommended workaround that keeps the instruction at the start of context

    Gemini uses "contents" with "parts" instead of "messages" with "content".
    Role mapping: OpenAI/Claude "assistant" → Gemini "model"
    """
    contents = []
    if system_prompt:
        # Gemini workaround: inject system prompt as first user/model exchange
        contents.append({"role": "user", "parts": [{"text": system_prompt}]})
        contents.append({"role": "model", "parts": [{"text": "Understood."}]})

    # Convert standard messages format to Gemini's "contents" format
    for m in messages:
        role = "model" if m["role"] == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": m["content"]}]})

    payload = {
        "contents": contents,
        "generationConfig": {"maxOutputTokens": max_tokens}
    }
    # API key goes in the URL query string for Gemini (not in a header)
    url = GEMINI_API_URL.format(model=model, key=api_key)
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data,
        headers={"content-type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read())
        # Gemini response: {"candidates": [{"content": {"parts": [{"text": "..."}]}}]}
        return result["candidates"][0]["content"]["parts"][0]["text"].strip()


def call_ollama(base_url, model, messages, system_prompt="", max_tokens=1500):
    """
    Call a locally running Ollama instance (http://localhost:11434 by default).

    Why Ollama?
      - 100% offline — no API key, no internet required after model download
      - Ideal for users with privacy concerns or limited internet connectivity
      - Supports llama3, mistral, phi3, gemma and many more models

    Ollama uses OpenAI-compatible format with "stream": false for a single response.
    num_predict controls max output tokens in Ollama's options block.
    Timeout is 120s (vs 60s for cloud) because local inference can be slower on CPU.
    """
    all_messages = []
    if system_prompt:
        # Ollama supports the standard "system" role in the messages array
        all_messages.append({"role": "system", "content": system_prompt})
    all_messages.extend(messages)

    payload = {
        "model": model,
        "messages": all_messages,
        "stream": False,                        # Return complete response, not streaming chunks
        "options": {"num_predict": max_tokens}  # Ollama's equivalent of max_tokens
    }
    url = base_url.rstrip("/") + "/api/chat"    # Ollama chat endpoint
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data,
        headers={"content-type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:
        # Ollama response: {"message": {"role": "assistant", "content": "..."}}
        return json.loads(resp.read())["message"]["content"].strip()


def call_openai_compat(api_key, base_url, model, messages, system_prompt="", max_tokens=1500):
    """
    Call any OpenAI-compatible API endpoint.

    Why one function for OpenAI, MiniMax, and Qwen?
      - These providers all follow the OpenAI Chat Completions API format
      - The only differences are the base_url and model name
      - Reusing this function reduces code duplication and makes adding new
        compatible providers a one-line change in call_llm()

    Used for: OpenAI (api.openai.com), MiniMax (api.minimax.chat),
              Qwen/DashScope (dashscope.aliyuncs.com/compatible-mode)
    """
    all_messages = []
    if system_prompt:
        # Standard OpenAI "system" role — sets behavior context for the conversation
        all_messages.append({"role": "system", "content": system_prompt})
    all_messages.extend(messages)

    payload = {"model": model, "messages": all_messages, "max_tokens": max_tokens}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",   # Standard Bearer token auth
            "content-type": "application/json"
        },
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        # OpenAI response: {"choices": [{"message": {"content": "..."}}]}
        return json.loads(resp.read())["choices"][0]["message"]["content"].strip()


def call_llm(messages, system_prompt="", max_tokens=1500):
    """
    Central dispatcher — reads the active provider from config and routes
    to the appropriate provider-specific function.

    Why this pattern?
      - All AI features (explain, generate questions, study plan) call this one
        function instead of hard-coding Claude logic everywhere
      - Switching the user's AI provider in Settings instantly changes which
        function is called — no restart needed, no code changes

    Returns: (text, error) tuple
      - On success: (response_text, None)
      - On failure: (None, error_message_string)

    The caller checks: if error: return {"error": error}, 502
    """
    config = load_config()
    provider = config.get("provider", "claude")  # Default to Claude if not set
    providers = config.get("providers", {})
    pc = providers.get(provider, {})             # Config dict for the active provider

    try:
        if provider == "claude":
            # Support legacy config (apiKey at root) and new nested format
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
            # Ollama needs no API key — just a base URL and model name
            base_url = pc.get("baseUrl", "http://localhost:11434")
            model = pc.get("model", "llama3")
            text = call_ollama(base_url, model, messages, system_prompt, max_tokens)

        elif provider == "openai":
            api_key = pc.get("apiKey", "")
            if not api_key:
                return None, "No OpenAI API key configured."
            model = pc.get("model", "gpt-4o-mini")
            text = call_openai_compat(api_key, "https://api.openai.com/v1",
                                      model, messages, system_prompt, max_tokens)

        elif provider == "minimax":
            # MiniMax is an OpenAI-compatible API based in China
            api_key = pc.get("apiKey", "")
            if not api_key:
                return None, "No MiniMax API key configured."
            model = pc.get("model", "abab6.5s-chat")
            text = call_openai_compat(api_key, "https://api.minimax.chat/v1",
                                      model, messages, system_prompt, max_tokens)

        elif provider == "qwen":
            # Alibaba Cloud Qwen via DashScope's OpenAI-compatible endpoint
            api_key = pc.get("apiKey", "")
            if not api_key:
                return None, "No Qwen API key configured."
            model = pc.get("model", "qwen-turbo")
            text = call_openai_compat(api_key,
                                      "https://dashscope.aliyuncs.com/compatible-mode/v1",
                                      model, messages, system_prompt, max_tokens)

        else:
            return None, f"Unknown provider: {provider}"

        return text, None  # Success — return text and no error

    except urllib.error.HTTPError as e:
        # Capture the API error body (e.g., "invalid_api_key") for debugging
        body_err = e.read().decode()[:300]
        return None, f"API error {e.code}: {body_err}"
    except Exception as e:
        return None, str(e)


# ═══════════════════════════════════════════════════════════════════════════════
# DATABASE SETUP
# SQLite is used because:
#   - Zero configuration — no database server to install or run
#   - The .db file lives next to the app, making backup/restore trivial
#   - Supports concurrent reads and single-writer, which is fine for one user
#   - CREATE TABLE IF NOT EXISTS means schema is safe to re-run on every startup
# ═══════════════════════════════════════════════════════════════════════════════

def get_db():
    """
    Open a connection to the SQLite database.
    row_factory = sqlite3.Row enables dict-like access (row["column_name"])
    instead of positional tuples, making code much more readable.
    Callers are responsible for calling conn.close() after use.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """
    Create all database tables on first run (or safely no-op if they exist).
    Called once at server startup before any requests are served.

    Table design rationale:
      answers        — append-only log of every question answered; never updated
      wrong_answers  — mutable summary of questions user keeps getting wrong
      study_sessions — one row per calendar day for streak/activity tracking
      meta           — key-value store for single values like last_study_date
      claude_cache   — server-side cache for AI explanations to avoid repeat API calls
      mock_exams     — stores CBT exam sessions from start through completion
    """
    conn = get_db()
    c = conn.cursor()

    # answers: Raw event log — one row per question answered.
    # Append-only design means we never lose history; analytics query this table.
    c.execute("""
        CREATE TABLE IF NOT EXISTS answers (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            q_id        INTEGER NOT NULL,      -- Foreign key to question ID
            domain_id   INTEGER NOT NULL,      -- 1-8 for CISSP domains
            is_correct  INTEGER NOT NULL,      -- 1 = correct, 0 = wrong (SQLite has no BOOLEAN)
            answered_at TEXT NOT NULL DEFAULT (date('now'))  -- ISO date string YYYY-MM-DD
        )
    """)

    # wrong_answers: Aggregated view of incorrect answers for the Review tab.
    # q_id is PRIMARY KEY so there's one row per question regardless of how many
    # times it was answered wrong. count increments on each wrong attempt.
    c.execute("""
        CREATE TABLE IF NOT EXISTS wrong_answers (
            q_id        INTEGER PRIMARY KEY,
            domain_id   INTEGER NOT NULL,
            question_text TEXT,       -- First 120 chars of question for display
            count       INTEGER NOT NULL DEFAULT 1,   -- Number of times answered wrong
            last_wrong  TEXT NOT NULL  -- Date of most recent wrong answer (ISO string)
        )
    """)

    # study_sessions: One row per calendar day the user studied.
    # Used to calculate the streak and render the activity heatmap calendar.
    # session_date is PRIMARY KEY to enforce one-row-per-day constraint.
    c.execute("""
        CREATE TABLE IF NOT EXISTS study_sessions (
            session_date    TEXT PRIMARY KEY,  -- YYYY-MM-DD
            questions_done  INTEGER DEFAULT 0,
            correct         INTEGER DEFAULT 0
        )
    """)

    # meta: Simple key-value store for miscellaneous persistent values.
    # Currently stores 'last_study_date' for dashboard display.
    c.execute("""
        CREATE TABLE IF NOT EXISTS meta (
            key     TEXT PRIMARY KEY,
            value   TEXT
        )
    """)

    # claude_cache: Stores AI explanations so we don't call the API twice
    # for the same question. Composite primary key (q_id, is_correct) because
    # the explanation differs based on whether the user answered correctly.
    c.execute("""
        CREATE TABLE IF NOT EXISTS claude_cache (
            q_id        INTEGER NOT NULL,
            is_correct  INTEGER NOT NULL,   -- 1 = correct response, 0 = wrong response
            response    TEXT NOT NULL,      -- JSON-encoded explanation dict
            created_at  TEXT NOT NULL,      -- Date cached (for future TTL logic)
            PRIMARY KEY (q_id, is_correct)  -- One cache entry per question per outcome
        )
    """)

    # mock_exams: Stores the full state of each CBT exam session.
    # question_ids, answers, domain_results are stored as JSON strings because
    # SQLite doesn't have a native array/object type. json.loads() on read.
    c.execute("""
        CREATE TABLE IF NOT EXISTS mock_exams (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at      TEXT NOT NULL,      -- ISO datetime when exam started
            completed_at    TEXT,               -- NULL until submitted
            duration_limit  INTEGER DEFAULT 240, -- Allowed minutes (240 = 4 hours)
            question_ids    TEXT NOT NULL,       -- JSON array: [1001, 2005, 3012, ...]
            answers         TEXT DEFAULT '{}',   -- JSON dict: {"1001": 2, "2005": 0, ...}
            domain_results  TEXT DEFAULT '{}',   -- JSON dict: {"1": {"answered":20,"correct":15}}
            total_questions INTEGER DEFAULT 0,
            correct_count   INTEGER DEFAULT 0,
            score           REAL,                -- Percentage 0.0-100.0
            status          TEXT DEFAULT 'in_progress'  -- 'in_progress' | 'completed'
        )
    """)

    conn.commit()
    conn.close()
    print(f"  ✅ Database ready: {DB_PATH}")


# ═══════════════════════════════════════════════════════════════════════════════
# API LOGIC — STUDY PROGRESS
# ═══════════════════════════════════════════════════════════════════════════════

def get_full_state():
    """
    Build and return the complete user progress snapshot for the frontend.
    Called by GET /api/state on every page load so the UI has current data.

    Why aggregate in Python rather than in SQL?
      - Streak calculation requires Python date arithmetic (not easy in SQLite)
      - Returning one combined JSON object reduces the number of HTTP round-trips

    Returns: dict with totalAnswered, totalCorrect, domainStats, wrongAnswers,
             studyDays, dailyActivity, streakDays, lastStudyDate
    """
    conn = get_db()
    c = conn.cursor()

    # Overall totals — SUM(is_correct) counts the 1s (correct answers)
    c.execute("SELECT COUNT(*) as total, SUM(is_correct) as correct FROM answers")
    row = c.fetchone()
    total   = row["total"] or 0
    correct = int(row["correct"] or 0)

    # Per-domain accuracy for the radar chart and domain-level stats
    c.execute("""
        SELECT domain_id, COUNT(*) as answered, SUM(is_correct) as correct
        FROM answers GROUP BY domain_id
    """)
    domain_stats = {}
    for r in c.fetchall():
        domain_stats[str(r["domain_id"])] = {
            "answered": r["answered"],
            "correct":  int(r["correct"] or 0)
        }

    # Wrong answers sorted by frequency (most-missed questions first)
    c.execute("SELECT * FROM wrong_answers ORDER BY count DESC")
    wrong = []
    for r in c.fetchall():
        wrong.append({
            "qId":          r["q_id"],
            "domainId":     r["domain_id"],
            "questionText": r["question_text"],
            "count":        r["count"],
            "lastWrong":    r["last_wrong"]
        })

    # All study dates — used by streak logic and calendar heatmap
    c.execute("SELECT session_date FROM study_sessions ORDER BY session_date")
    study_days = [r["session_date"] for r in c.fetchall()]

    # Last 30 days of activity for the dashboard calendar
    c.execute("""
        SELECT session_date as d, questions_done as answered, correct
        FROM study_sessions
        ORDER BY session_date DESC LIMIT 30
    """)
    daily_activity = {}
    for r in c.fetchall():
        daily_activity[r["d"]] = {"answered": r["answered"], "correct": r["correct"]}

    # Streak: count consecutive days backwards from today
    # Uses a set lookup on study_days strings for O(1) membership check
    streak     = 0
    today      = datetime.now().date()
    check_date = today
    while check_date.isoformat() in study_days or str(check_date) in study_days:
        streak    += 1
        check_date -= timedelta(days=1)

    # Last study date for "last studied X days ago" display
    c.execute("SELECT value FROM meta WHERE key='last_study_date'")
    row = c.fetchone()
    last_study_date = row["value"] if row else None

    conn.close()
    return {
        "totalAnswered":  total,
        "totalCorrect":   correct,
        "domainStats":    domain_stats,
        "wrongAnswers":   wrong,
        "studyDays":      study_days,
        "dailyActivity":  daily_activity,
        "streakDays":     streak,
        "lastStudyDate":  last_study_date
    }


def record_answer(q_id, domain_id, is_correct, question_text):
    """
    Persist one answered question to the database.
    Called by POST /api/answer after the user picks an option in the quiz.

    Three writes in one transaction:
      1. answers     — append to the event log (always)
      2. study_sessions — increment today's counter (upsert)
      3. wrong_answers  — increment wrong count (upsert, only if wrong)

    Why ON CONFLICT ... DO UPDATE (upsert)?
      - study_sessions has session_date as PRIMARY KEY
      - wrong_answers  has q_id as PRIMARY KEY
      - Upsert avoids a separate SELECT + INSERT/UPDATE round-trip
    """
    today = datetime.now().date().isoformat()  # YYYY-MM-DD
    conn  = get_db()
    c     = conn.cursor()

    # 1. Append to answer log
    c.execute("INSERT INTO answers (q_id, domain_id, is_correct, answered_at) VALUES (?,?,?,?)",
              (q_id, domain_id, 1 if is_correct else 0, today))

    # 2. Upsert today's study session counter
    c.execute("""
        INSERT INTO study_sessions (session_date, questions_done, correct)
        VALUES (?, 1, ?)
        ON CONFLICT(session_date) DO UPDATE SET
            questions_done = questions_done + 1,
            correct        = correct + ?
    """, (today, 1 if is_correct else 0, 1 if is_correct else 0))

    # 3. Track wrong answers — only insert/increment on failure
    if not is_correct:
        c.execute("""
            INSERT INTO wrong_answers (q_id, domain_id, question_text, count, last_wrong)
            VALUES (?, ?, ?, 1, ?)
            ON CONFLICT(q_id) DO UPDATE SET
                count      = count + 1,
                last_wrong = ?
        """, (q_id, domain_id,
              question_text[:120] if question_text else "",  # Truncate to save space
              today, today))

    # 4. Keep last_study_date in meta for "last studied" display
    c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('last_study_date', ?)", (today,))

    conn.commit()
    conn.close()
    return {"ok": True}


def import_state(data):
    """
    Replace all database content with data from a JSON progress export.
    Used when migrating from localStorage to the SQLite backend, or when
    restoring from a backup.

    Why DELETE all then re-insert?
      - Simpler and safer than trying to merge/diff existing rows
      - Import is an intentional overwrite operation — user confirmed via dialog
    """
    conn = get_db()
    c    = conn.cursor()

    # Clear all existing data before importing
    c.execute("DELETE FROM answers")
    c.execute("DELETE FROM wrong_answers")
    c.execute("DELETE FROM study_sessions")
    c.execute("DELETE FROM meta")

    # Re-insert daily activity as study_sessions rows
    for date, activity in data.get("dailyActivity", {}).items():
        c.execute("""
            INSERT OR REPLACE INTO study_sessions (session_date, questions_done, correct)
            VALUES (?, ?, ?)
        """, (date, activity.get("answered", 0), activity.get("correct", 0)))

    # Re-insert wrong answers list
    for w in data.get("wrongAnswers", []):
        c.execute("""
            INSERT OR REPLACE INTO wrong_answers (q_id, domain_id, question_text, count, last_wrong)
            VALUES (?, ?, ?, ?, ?)
        """, (w.get("qId"), w.get("domainId"),
              w.get("questionText", ""),
              w.get("count", 1),
              w.get("lastWrong", "")))

    # Restore last study date metadata
    if data.get("lastStudyDate"):
        c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('last_study_date', ?)",
                  (data["lastStudyDate"],))

    conn.commit()
    conn.close()
    return {"ok": True, "imported": True}


def clear_wrong_answers():
    """
    Delete all rows from wrong_answers table.
    Called when user clicks "Clear All" in the Wrong Answers review tab.
    Simple delete — no cascade needed since wrong_answers is standalone.
    """
    conn = get_db()
    conn.execute("DELETE FROM wrong_answers")
    conn.commit()
    conn.close()
    return {"ok": True}


def get_history(domain_id=None, limit=200):
    """
    Return recent answer history, optionally filtered by domain.
    Used for debugging and domain-level drill-down analytics.
    Limit 200 prevents sending huge payloads on old databases with many answers.
    """
    conn = get_db()
    c    = conn.cursor()
    if domain_id:
        c.execute("SELECT * FROM answers WHERE domain_id=? ORDER BY id DESC LIMIT ?",
                  (domain_id, limit))
    else:
        c.execute("SELECT * FROM answers ORDER BY id DESC LIMIT ?", (limit,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


# ═══════════════════════════════════════════════════════════════════════════════
# AI EXPLANATION CACHE
# Calling an LLM costs money and takes 1-3 seconds.
# Caching stores the result in SQLite so re-visiting the same question shows
# the explanation instantly without another API call.
# Cache key: (q_id, is_correct) because the explanation differs depending on
# whether the student answered correctly or not.
# ═══════════════════════════════════════════════════════════════════════════════

def get_claude_cache(q_id, is_correct):
    """
    Look up a cached AI explanation for a specific question+outcome pair.
    Returns the parsed dict if found, None if not cached yet.
    """
    conn = get_db()
    c    = conn.cursor()
    c.execute("SELECT response FROM claude_cache WHERE q_id=? AND is_correct=?",
              (q_id, 1 if is_correct else 0))
    row = c.fetchone()
    conn.close()
    if row:
        try:
            return json.loads(row["response"])   # Deserialize JSON string back to dict
        except Exception:
            return None  # Corrupted cache entry — treat as cache miss
    return None


def set_claude_cache(q_id, is_correct, response_dict):
    """
    Store an AI explanation in the cache after a successful API call.
    INSERT OR REPLACE handles the edge case where a question was explained
    twice in the same session (updates the entry with the newest response).
    """
    conn  = get_db()
    today = datetime.now().date().isoformat()
    conn.execute("""
        INSERT OR REPLACE INTO claude_cache (q_id, is_correct, response, created_at)
        VALUES (?, ?, ?, ?)
    """, (q_id, 1 if is_correct else 0, json.dumps(response_dict), today))
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════════════════════════════════
# LLM HANDLERS — FEATURE-SPECIFIC AI LOGIC
# ═══════════════════════════════════════════════════════════════════════════════

def _parse_llm_json(text):
    """
    Robustly extract and parse a JSON object from raw LLM output.

    Why multiple strategies?
      LLMs often ignore instructions like "return only JSON" and wrap their
      response in markdown code fences (```json ... ```) or add explanatory text
      before/after the JSON. This function tries multiple approaches so the UI
      never displays raw JSON to the user.

    Strategy 1 — Strip code fences:
      Regex removes ```json ... ``` (any language tag, with or without newline)
    Strategy 2 — Direct parse of stripped text:
      Works for clean JSON after fence removal
    Strategy 3 — Direct parse of original:
      Handles the case where there were no fences to begin with
    Strategy 4 — Regex extract {...}:
      Handles preamble text like "Here is your JSON:" before the object
    Strategy 5 — Regex extract [...]:
      Fallback for JSON arrays (used by generate-questions endpoint)
    """
    import re
    text = text.strip()

    # Strategy 1: Remove all code fence forms with regex
    # ^```[a-zA-Z]* matches ```json, ```JSON, ```, etc. at start of string
    # \s* after the tag matches newline OR space between "json" and "{"
    fence_stripped = re.sub(r'^```[a-zA-Z]*\s*', '', text)   # Remove opening fence + lang tag
    fence_stripped = re.sub(r'\s*```$', '', fence_stripped)   # Remove closing fence
    fence_stripped = fence_stripped.strip()

    # Strategy 2: Direct parse after fence strip (most common success path)
    try:
        return json.loads(fence_stripped)
    except (json.JSONDecodeError, ValueError):
        pass

    # Strategy 3: Direct parse of original (already clean JSON, no fences)
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass

    # Strategy 4: Regex extract first {...} block — handles LLMs that add preamble text
    match = re.search(r'\{[\s\S]*\}', fence_stripped or text)
    if match:
        try:
            return json.loads(match.group(0))
        except (json.JSONDecodeError, ValueError):
            pass

    # Strategy 5: Regex extract first [...] array — fallback for question generation
    match = re.search(r'\[[\s\S]*\]', fence_stripped or text)
    if match:
        try:
            return json.loads(match.group(0))
        except (json.JSONDecodeError, ValueError):
            pass

    # All strategies exhausted — raise so the caller can return a user-friendly error
    raise ValueError(f"Could not parse JSON. Preview: {text[:300]}")


def handle_claude_explain(body):
    """
    Generate an AI explanation for a quiz question the user just answered.

    Flow:
      1. Check SQLite cache — return immediately if previously explained
      2. Build a context-aware prompt (different for correct vs. wrong answers)
      3. Call the active LLM provider via call_llm()
      4. Parse JSON response with _parse_llm_json()
      5. Cache the result for future visits to the same question
      6. Return the explanation dict to the frontend

    Why different prompts for correct vs. wrong answers?
      - Wrong answer: focus on WHY the user's choice was wrong and what to study
      - Correct answer: reinforce understanding and reveal why distractors fail
      Both improve retention, just from different angles.

    The _cached flag tells the frontend to show a "cached" badge instead
    of a loading spinner, so users know they're seeing a stored result.
    """
    q_id               = body.get("qId")
    question           = body.get("question", "")
    options            = body.get("options", [])
    correct_answer     = body.get("correctAnswer", "")
    user_answer        = body.get("userAnswer", "")
    is_correct         = body.get("isCorrect", False)
    domain             = body.get("domain", "")
    existing_explanation = body.get("existingExplanation", "")

    # Cache hit → return immediately (saves API cost + latency)
    if q_id is not None:
        cached = get_claude_cache(q_id, is_correct)
        if cached:
            cached["_cached"] = True   # Tell frontend this came from cache
            return cached, 200

    system_prompt = (
        "You are an expert CISSP exam coach with deep knowledge of all 8 CISSP domains. "
        "Help candidates deeply understand every question — whether they got it right or wrong. "
        "Be concise, exam-focused, and practical. Do not pad your response."
    )

    # Format options as "A. text\nB. text\n..." for readability in the prompt
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

    text, error = call_llm([{"role": "user", "content": user_message}],
                           system_prompt, max_tokens=900)
    if error:
        return {"error": error}, 502

    try:
        parsed = _parse_llm_json(text)
    except Exception:
        # If JSON parse fails completely, surface the raw text in whyCorrect/whyWrong
        # so the user still gets useful information rather than an empty response
        parsed = {"whyCorrect": text, "whyWrong": "", "studyFocus": "", "memoryTip": ""} \
                 if is_correct \
                 else {"whyWrong": text, "whyCorrect": "", "studyFocus": "", "memoryTip": ""}

    # Cache for future requests to this question
    if q_id is not None:
        set_claude_cache(q_id, is_correct, parsed)

    return parsed, 200


def handle_claude_generate_questions(body):
    """
    Ask the AI to generate new CISSP practice questions for a specific domain.

    Why cap at 10 questions?
      - LLMs produce lower quality at higher counts (repetition, hallucination)
      - 5-10 new questions per request keeps quality high and latency low

    ID assignment: new IDs start at max(existing_ids) + 1 to avoid collisions
    with the built-in question bank (IDs 1-60) and JSON files (IDs 1001-8025).
    """
    domain_id    = body.get("domain", 1)
    domain_name  = body.get("domainName", "")
    count        = min(int(body.get("count", 5)), 10)  # Hard cap at 10
    existing_ids = body.get("existingIds", [])
    max_existing_id = max(existing_ids) if existing_ids else 100  # Start after existing IDs

    system_prompt = (
        "You are a CISSP exam question writer with expertise in all 8 CISSP domains. "
        "Generate realistic, exam-quality multiple-choice questions that test deep understanding. "
        "Return ONLY valid JSON — no markdown, no explanation outside the JSON."
    )

    # Provide the exact JSON schema in the prompt to minimize format errors
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

    text, error = call_llm([{"role": "user", "content": user_message}],
                           system_prompt, max_tokens=2500)
    if error:
        return {"error": error}, 502

    try:
        questions = _parse_llm_json(text)
        if not isinstance(questions, list):
            questions = [questions]  # Wrap single object in list if LLM omitted the array
        return {"questions": questions}, 200
    except Exception as e:
        return {"error": f"Failed to parse questions: {str(e)}", "raw": text[:500]}, 500


def handle_claude_study_plan(body):
    """
    Generate a personalized multi-week CISSP study plan based on the user's
    actual performance data and their target exam date.

    Why include domain weights in the prompt?
      CISSP exam weights (D1=16%, D7=13%, etc.) should influence how much time
      the student spends on each domain. A domain at 60% accuracy but only 10%
      exam weight is less urgent than a domain at 70% with 16% weight.

    JSON parsing is especially important here because study plans are long
    (2000+ tokens) and LLMs frequently wrap them in code fences. The prompt
    explicitly says "no code fences" AND _parse_llm_json tries to handle them
    anyway — defense in depth.
    """
    domain_stats  = body.get("domainStats", {})
    target_date   = body.get("targetDate", "")

    # Official CISSP domain names and their exam percentage weights
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

    # Build a human-readable performance summary for the prompt
    # E.g.: "  D1 (Security & Risk Management, 16%): 73% (11/15)"
    perf_lines = []
    for did, stats in domain_stats.items():
        answered = stats.get("answered", 0)
        correct  = stats.get("correct", 0)
        pct      = round(correct / answered * 100) if answered > 0 else None
        name     = domain_names.get(did, f"Domain {did}")
        weight   = domain_weights.get(did, "?")
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

    # The JSON schema in the prompt constrains the output structure so the
    # frontend's renderStudyPlan() function can reliably access named fields
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

    text, error = call_llm([{"role": "user", "content": user_message}],
                           system_prompt, max_tokens=2500)
    if error:
        return {"error": error}, 502

    try:
        return _parse_llm_json(text), 200
    except Exception:
        # Last resort: brute-force regex on the raw LLM text
        import re
        match = re.search(r'\{[\s\S]*\}', text)
        if match:
            try:
                return json.loads(match.group(0)), 200
            except Exception:
                pass
        # Truly unrecoverable — surface a clear error message to the user
        return {"error": f"AI returned an unreadable response. Please try again. "
                         f"(Parse failed on: {text[:120]}...)"}, 502


# ═══════════════════════════════════════════════════════════════════════════════
# MOCK EXAM — CBT SIMULATOR
# Replicates the actual CISSP Computer-Based Test experience:
#   - Domain-proportional question sampling (matching official exam weights)
#   - 4-hour (240-minute) timer
#   - Results stored in SQLite for history tracking (last 5 attempts)
# ═══════════════════════════════════════════════════════════════════════════════

def start_mock_exam(body):
    """
    Create a new mock exam session and persist it to the database.

    Domain-weighted sampling logic:
      The real CISSP exam draws questions proportionally from each domain.
      For a 125-question exam: D1 (16%) → ~20 questions, D2 (10%) → ~13, etc.
      We replicate this using random.sample() per domain pool.

    Why random.sample() (without replacement)?
      Ensures no duplicate questions within one exam session.
      random.sample(pool, n) raises ValueError if n > len(pool), so we
      use min(n, len(pool)) to gracefully handle small question pools.

    Returns: examId (for the client to track), selected question IDs, duration
    """
    import random
    all_questions = load_question_files()          # Load from cache
    num_questions = int(body.get("numQuestions", 125))
    duration      = int(body.get("durationMinutes", 240))

    if not all_questions:
        return {"error": "No question files found. Ensure questions/ directory exists."}, 400

    # Group question IDs by domain for per-domain sampling
    by_domain = {}
    for q in all_questions:
        d = str(q.get("domain", 1))
        by_domain.setdefault(d, []).append(q["id"])

    # Official CISSP 2024 domain weights (must sum to 100)
    weights = {"1": 16, "2": 10, "3": 13, "4": 13, "5": 13, "6": 12, "7": 13, "8": 10}
    selected_ids  = []
    total_weight  = sum(weights.values())  # = 100

    for did, w in weights.items():
        pool = by_domain.get(did, [])
        if pool:
            # Proportional target: e.g., 125 * 16/100 = 20 for Domain 1
            n = max(1, round(num_questions * w / total_weight))
            selected_ids.extend(random.sample(pool, min(n, len(pool))))

    # Shuffle and trim to exact target count
    # (rounding above may result in slightly more/fewer than requested)
    random.shuffle(selected_ids)
    selected_ids = selected_ids[:num_questions]

    # Persist the new exam session with status='in_progress'
    now  = datetime.now().isoformat()
    conn = get_db()
    cur  = conn.cursor()
    cur.execute("""
        INSERT INTO mock_exams (started_at, duration_limit, question_ids, total_questions, status)
        VALUES (?, ?, ?, ?, 'in_progress')
    """, (now, duration, json.dumps(selected_ids), len(selected_ids)))
    exam_id = cur.lastrowid   # SQLite auto-increment ID for this exam session
    conn.commit()
    conn.close()

    return {
        "examId":          exam_id,
        "questionIds":     selected_ids,
        "totalQuestions":  len(selected_ids),
        "durationMinutes": duration
    }, 200


def submit_mock_exam(body):
    """
    Grade a completed mock exam and update the database with results.

    Grading logic:
      - Look up each question by ID to get the correct answer index
      - Compare against the user's submitted answer index
      - Aggregate per-domain accuracy for the results breakdown chart

    Pass threshold: 70% overall (maps to CISSP's 700/1000 passing score).
    Note: Real CISSP uses a psychometric scaled score, not a raw percentage,
    but 70% is a reasonable practice proxy.

    Why check status != 'in_progress'?
      Prevents double-submission if the user reloads the results page,
      which would incorrectly reset the score.
    """
    exam_id = body.get("examId")
    answers = body.get("answers", {})   # {q_id_str: selected_option_index}

    conn = get_db()
    cur  = conn.cursor()
    cur.execute("SELECT * FROM mock_exams WHERE id=?", (exam_id,))
    row = cur.fetchone()

    if not row:
        conn.close()
        return {"error": "Exam not found"}, 404
    if row["status"] != "in_progress":
        conn.close()
        return {"error": "Exam already completed"}, 400

    # Build a lookup map {str(id): question_dict} for O(1) grading per question
    all_questions = load_question_files()
    q_map         = {str(q["id"]): q for q in all_questions}
    question_ids  = json.loads(row["question_ids"])

    correct_count  = 0
    domain_results = {}   # {"1": {"answered": N, "correct": N}, ...}
    answer_details = {}   # {"qId": {"selected": idx, "correct": idx, "isCorrect": bool}}

    for qid in question_ids:
        q = q_map.get(str(qid))
        if not q:
            continue   # Skip if question was removed from question bank since exam started

        did      = str(q.get("domain", 1))
        selected = answers.get(str(qid))  # None if question was skipped
        is_correct = (selected is not None and int(selected) == int(q["answer"]))

        if is_correct:
            correct_count += 1

        # Accumulate per-domain stats for the results domain breakdown
        domain_results.setdefault(did, {"answered": 0, "correct": 0})
        domain_results[did]["answered"] += 1
        if is_correct:
            domain_results[did]["correct"] += 1

        # Store individual answer details for the review mode
        answer_details[str(qid)] = {
            "selected":  selected,
            "correct":   q["answer"],
            "isCorrect": is_correct
        }

    total = len(question_ids)
    score = round(correct_count / total * 100, 1) if total > 0 else 0
    now   = datetime.now().isoformat()

    # Update exam row with final results and mark as completed
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
        "examId":        exam_id,
        "score":         score,
        "correctCount":  correct_count,
        "totalQuestions": total,
        "domainResults": domain_results,
        "answerDetails": answer_details,
        "passed":        score >= 70   # 70% = approximate CISSP pass threshold
    }, 200


def get_mock_exam_history():
    """
    Return the last 5 mock exam attempts for the history view.
    LIMIT 5 matches the UI's "last 5 attempts" design — prevents old exams
    from cluttering the history while keeping recent progress visible.
    """
    conn = get_db()
    cur  = conn.cursor()
    cur.execute("""
        SELECT id, started_at, completed_at, total_questions,
               correct_count, score, status, duration_limit
        FROM mock_exams ORDER BY id DESC LIMIT 5
    """)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return {"exams": rows}, 200


def get_mock_exam_detail(exam_id):
    """
    Return the full detail of one exam including all questions, answers,
    and domain breakdown. Used by the review mode after exam completion.
    JSON fields stored as strings in SQLite are deserialized back to dicts/lists.
    """
    conn = get_db()
    cur  = conn.cursor()
    cur.execute("SELECT * FROM mock_exams WHERE id=?", (exam_id,))
    row  = cur.fetchone()
    conn.close()

    if not row:
        return {"error": "Not found"}, 404

    d = dict(row)
    # Deserialize the three JSON-string columns back to Python objects
    for field in ("question_ids", "answers", "domain_results"):
        try:
            d[field] = json.loads(d[field] or "null")
        except Exception:
            pass   # Leave as string if deserialization fails
    return d, 200


# ═══════════════════════════════════════════════════════════════════════════════
# HTTP REQUEST HANDLER
# Extends BaseHTTPRequestHandler from Python's stdlib http.server.
# Handles GET (read data / serve HTML) and POST (write data / call AI).
# OPTIONS is required for CORS preflight — browsers send it before cross-origin
# POST requests, even on localhost, to check allowed methods and headers.
# ═══════════════════════════════════════════════════════════════════════════════

class Handler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        """
        Override default verbose access logging.
        Only print 404/500 errors — success requests are silently ignored
        so the terminal stays readable during active study sessions.
        """
        if "404" in str(args) or "500" in str(args):
            print(f"  ⚠️  {args}")

    def send_json(self, data, status=200):
        """
        Serialize a Python dict to JSON and write it as the HTTP response.
        Sets Content-Length so the browser knows when the body ends.
        CORS headers (Access-Control-Allow-*) are required because the HTML
        file may be opened directly from disk (file://) rather than via the server.
        """
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")      # Allow any origin
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def send_cors_preflight(self):
        """
        Respond to browser CORS preflight OPTIONS request.
        The browser sends OPTIONS before a cross-origin POST to verify
        that the server accepts requests from the page's origin.
        """
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_OPTIONS(self):
        """Handle CORS preflight — required before any cross-origin POST."""
        self.send_cors_preflight()

    def do_GET(self):
        """
        Route GET requests:
          /           → serve CISSP_Study_App.html (the SPA)
          /api/*      → call the appropriate data function and return JSON

        Path and query string are parsed separately so /api/questions?domain=3
        correctly filters to domain 3.
        """
        parsed = urlparse(self.path)
        path   = parsed.path
        qs     = parse_qs(parsed.query)   # {'domain': ['3']} style dict

        # Serve the main HTML file for root and /index.html
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

        # ── API route dispatch ────────────────────────────────────────────────

        if path == "/api/state":
            # Full user progress snapshot — called on every page load
            self.send_json(get_full_state())

        elif path == "/api/history":
            # Answer history, optionally filtered by domain
            domain_id = int(qs["domain"][0]) if "domain" in qs else None
            self.send_json(get_history(domain_id))

        elif path == "/api/ping":
            # Health check — frontend calls this to detect if backend is running
            # Also reports question bank size so the UI can show "X questions loaded"
            qs_count = len(load_question_files())
            self.send_json({"ok": True, "db": DB_PATH, "version": "4.0",
                           "questionBankSize": qs_count})

        elif path == "/api/config":
            # Return which providers are configured WITHOUT exposing actual keys
            config    = load_config()
            provider  = config.get("provider", "claude")
            providers = config.get("providers", {})

            # Build a map of {provider_name: has_credentials} — never leak keys
            configured = {}
            for p, pc in providers.items():
                configured[p] = bool(pc.get("apiKey") or pc.get("baseUrl"))

            # Legacy support: claude key stored at root level (old config format)
            if config.get("apiKey"):
                configured["claude"] = True

            self.send_json({
                "hasApiKey": bool(config.get("apiKey") or
                                  providers.get("claude", {}).get("apiKey")),
                "provider":           provider,
                "configuredProviders": configured
            })

        elif path == "/api/questions":
            # Serve question bank, optionally filtered by domain number
            domain_filter = int(qs["domain"][0]) if "domain" in qs else None
            questions     = load_question_files()
            if domain_filter:
                questions = [q for q in questions if q.get("domain") == domain_filter]
            self.send_json({"questions": questions, "total": len(questions)})

        elif path == "/api/mock-exam/history":
            result, status = get_mock_exam_history()
            self.send_json(result, status)

        elif path.startswith("/api/mock-exam/") and path.count("/") == 3:
            # /api/mock-exam/{id} — extract numeric ID from the last path segment
            try:
                exam_id        = int(path.split("/")[-1])
                result, status = get_mock_exam_detail(exam_id)
                self.send_json(result, status)
            except ValueError:
                self.send_json({"error": "Invalid exam ID"}, 400)

        else:
            self.send_json({"error": "Not found"}, 404)

    def do_POST(self):
        """
        Route POST requests — all data-writing and AI-calling operations.
        Body is parsed once from the request stream; all handlers receive
        the decoded dict. Returns 400 immediately if body is invalid JSON.
        """
        parsed = urlparse(self.path)
        path   = parsed.path

        # Read and parse the request body (Content-Length driven)
        length = int(self.headers.get("Content-Length", 0))
        body   = {}
        if length > 0:
            try:
                body = json.loads(self.rfile.read(length))
            except Exception:
                self.send_json({"error": "Invalid JSON"}, 400)
                return

        if path == "/api/answer":
            # Record one answered question — called after every quiz answer
            result = record_answer(
                body.get("qId"),
                body.get("domainId"),
                body.get("isCorrect", False),
                body.get("questionText", "")
            )
            self.send_json(result)

        elif path == "/api/import":
            # Bulk import from a localStorage JSON export
            result = import_state(body)
            self.send_json(result)

        elif path == "/api/clear-wrong":
            # Wipe wrong-answers list (user-initiated via "Clear All" button)
            result = clear_wrong_answers()
            self.send_json(result)

        elif path == "/api/config":
            # Save LLM provider configuration
            config = load_config()

            if "apiKey" in body:
                # Legacy flow: user enters a Claude API key directly
                api_key = body.get("apiKey", "").strip()
                if not api_key.startswith("sk-ant-"):
                    self.send_json({"error": "Invalid API key format. Must start with 'sk-ant-'"}, 400)
                    return
                # Store in both legacy root location AND new nested providers format
                # so both old and new code paths can find it
                config["apiKey"] = api_key
                config.setdefault("provider", "claude")
                config.setdefault("providers", {})
                config["providers"]["claude"] = {"apiKey": api_key}

            if "provider" in body:
                # User switched the active provider in Settings
                config["provider"] = body["provider"]

            if "providerConfig" in body:
                # User saved credentials for a specific provider
                # body format: {"provider": "gemini", "providerConfig": {"apiKey": "...", "model": "..."}}
                pc    = body["providerConfig"]
                pname = body.get("provider", config.get("provider", "claude"))
                config.setdefault("providers", {})[pname] = pc

            save_config(config)
            self.send_json({"ok": True})

        elif path == "/api/mock-exam/start":
            # Create a new exam session with domain-weighted question sampling
            result, status = start_mock_exam(body)
            self.send_json(result, status)

        elif path == "/api/mock-exam/submit":
            # Grade and finalize a completed exam session
            result, status = submit_mock_exam(body)
            self.send_json(result, status)

        elif path == "/api/claude/explain":
            # Get AI explanation for a quiz question
            result, status = handle_claude_explain(body)
            self.send_json(result, status)

        elif path == "/api/claude/generate-questions":
            # Generate new AI-crafted questions for a domain
            result, status = handle_claude_generate_questions(body)
            self.send_json(result, status)

        elif path == "/api/claude/study-plan":
            # Generate a personalized weekly study schedule
            result, status = handle_claude_study_plan(body)
            self.send_json(result, status)

        else:
            self.send_json({"error": "Not found"}, 404)


# ═══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

def open_browser():
    """
    Open the app in the default browser after a short delay.
    Runs in a daemon thread so it doesn't block server startup.
    1.5s delay gives the HTTPServer time to bind the port before the browser
    tries to connect — prevents "connection refused" on slow machines.
    daemon=True means this thread is auto-killed when the main process exits.
    """
    import time, webbrowser
    time.sleep(1.5)
    webbrowser.open(f"http://localhost:{PORT}")


if __name__ == "__main__":
    # ── Startup banner ────────────────────────────────────────────────────────
    print("\n" + "=" * 55)
    print("  🛡️  CISSP Study App — Local Server")
    print("=" * 55)

    init_db()                            # Create tables if they don't exist yet
    q_count = len(load_question_files()) # Pre-load question cache + report count

    print(f"  🌐 Server: http://localhost:{PORT}")
    print(f"  📂 Folder: {BASE_DIR}")
    print(f"  💾 Database: cissp_study.db")
    print(f"  📝 Question bank: {q_count} advanced questions loaded")

    # Show which AI provider is active and whether it has credentials
    config   = load_config()
    provider = config.get("provider", "claude")
    has_key  = bool(
        config.get("apiKey") or
        config.get("providers", {}).get(provider, {}).get("apiKey") or
        config.get("providers", {}).get(provider, {}).get("baseUrl")
    )
    if has_key:
        print(f"  🤖 AI Provider: {provider} ✅")
    else:
        print(f"  🤖 AI Provider: not configured (add key in Settings)")

    print(f"\n  Open http://localhost:{PORT} in your browser.")
    print("  Press Ctrl+C to stop.\n")

    # Launch browser in background thread so server starts first
    threading.Thread(target=open_browser, daemon=True).start()

    # Start the HTTP server — blocks here until Ctrl+C
    try:
        server = HTTPServer(("localhost", PORT), Handler)
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  👋 Server stopped.")
        sys.exit(0)
