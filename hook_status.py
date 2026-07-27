"""Hook that writes the crab's per-session status, with chat-title labels.

Reads the hook's JSON payload from stdin to extract session_id and
transcript_path. Scans the transcript for the chat title — a user rename
(custom-title) wins over the auto-generated ai-title (the same title
Claude Code shows in its Recents UI). Stores a trimmed version as the
session label. No title yet → no label (the pill stays hidden until
Claude generates one).
"""

import json
import re
import sys
import time
from pathlib import Path

STATE_DIR = Path.home() / ".claude" / "pet" / "state"
SESSIONS_DIR = STATE_DIR / "sessions"
STATUS_FILE = STATE_DIR / "status.json"


def _short(text, max_words=6, max_chars=42):
    """Trim a title down to ~6 words / 42 chars so it fits in the pill."""
    if not text:
        return None
    text = re.sub(r"<[^>]+>", " ", text)
    words = [w for w in text.split() if w and any(c.isalnum() for c in w)]
    if not words:
        return None
    out = " ".join(words[:max_words])
    return out[:max_chars]


def extract_label(transcript_path):
    """Find a human-readable label for the session: a user rename
    (custom-title) if present, else the auto-generated ai-title.

    Deliberately does NOT fall back to the user's first message — Tian
    wants the chat NAME, not their prompt. No title yet → None, and the
    pill stays hidden until Claude generates one.
    """
    if not transcript_path:
        return None
    path = Path(transcript_path)
    if not path.exists():
        return None
    custom_title = None
    ai_title = None
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for i, raw in enumerate(f):
                if i > 5000:
                    break
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                except Exception:
                    continue
                if not isinstance(obj, dict):
                    continue
                t = obj.get("type")
                if t == "custom-title":
                    title = obj.get("customTitle")
                    if title:
                        custom_title = title  # last one wins (latest rename)
                elif t == "ai-title":
                    title = obj.get("aiTitle")
                    if title:
                        ai_title = title
    except Exception:
        pass
    return _short(custom_title) or _short(ai_title)


def main():
    state = sys.argv[1] if len(sys.argv) > 1 else "idle"
    session_id = None
    transcript_path = None
    try:
        payload = json.load(sys.stdin)
        if isinstance(payload, dict):
            session_id = payload.get("session_id")
            transcript_path = payload.get("transcript_path")
    except Exception:
        pass

    label = extract_label(transcript_path)

    # Preserve label across hooks that fire before ai-title exists.
    if session_id and not label:
        prev = SESSIONS_DIR / f"{session_id}.json"
        if prev.exists():
            try:
                label = json.loads(prev.read_text()).get("label") or None
            except Exception:
                pass

    body = {"state": state, "ts": time.time()}
    if label:
        body["label"] = label
    if transcript_path:
        body["transcript"] = transcript_path

    try:
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        body_json = json.dumps(body)
        if session_id:
            (SESSIONS_DIR / f"{session_id}.json").write_text(body_json)
        else:
            STATUS_FILE.write_text(body_json)
    except Exception as e:
        print(f"crab hook err: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
