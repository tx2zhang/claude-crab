"""PreToolUse hook: writes a pending request, waits for the pet's response,
then returns the user's allow/deny decision back to Claude Code.

Writes a debug line to state/gate.log every time it fires — if Tian clicks
"approve" and nothing happens, check that log to see whether the gate even
ran. No entries = the hook isn't wired (settings.json or matcher problem).
"""

import json
import sys
import time
from pathlib import Path

STATE = Path.home() / ".claude" / "pet" / "state"
PENDING = STATE / "pending.json"
RESPONSE = STATE / "response.json"
ALWAYS = STATE / "always_allow.json"
LOG = STATE / "gate.log"

TIMEOUT_SECONDS = 300


def log(msg):
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
    except Exception:
        pass


def emit(decision, reason=None):
    out = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
        }
    }
    if reason:
        out["hookSpecificOutput"]["permissionDecisionReason"] = reason
    log(f"emit {decision}" + (f" ({reason})" if reason else ""))
    print(json.dumps(out))


def load_always():
    try:
        return set(json.loads(ALWAYS.read_text()))
    except Exception:
        return set()


def save_always(tools):
    try:
        ALWAYS.parent.mkdir(parents=True, exist_ok=True)
        ALWAYS.write_text(json.dumps(sorted(tools)))
    except Exception:
        pass


def main():
    log("gate fired")
    try:
        data = json.load(sys.stdin)
    except Exception as e:
        log(f"stdin parse fail: {e}")
        emit("ask")
        return

    tool_name = data.get("tool_name", "?")
    tool_input = data.get("tool_input", {}) or {}
    log(f"tool={tool_name}")

    if tool_name in load_always():
        log(f"auto-allow {tool_name} (in always_allow)")
        emit("allow")
        return

    STATE.mkdir(parents=True, exist_ok=True)
    if RESPONSE.exists():
        try:
            RESPONSE.unlink()
        except Exception:
            pass

    try:
        PENDING.write_text(json.dumps({
            "tool_name": tool_name,
            "tool_input": tool_input,
            "ts": time.time(),
        }))
    except Exception as e:
        log(f"write pending fail: {e}")
        emit("ask")
        return

    log(f"waiting for response (up to {TIMEOUT_SECONDS}s)")
    deadline = time.time() + TIMEOUT_SECONDS
    decision = None
    while time.time() < deadline:
        if RESPONSE.exists():
            try:
                resp = json.loads(RESPONSE.read_text())
                decision = resp.get("decision", "deny")
            except Exception:
                decision = "deny"
            break
        time.sleep(0.1)

    for f in (PENDING, RESPONSE):
        try:
            f.unlink()
        except Exception:
            pass

    if decision is None:
        log("timeout - denying")
        emit("deny", "Crab pet timed out waiting for response")
        return

    log(f"user said {decision}")
    if decision == "always":
        existing = load_always()
        existing.add(tool_name)
        save_always(existing)
        emit("allow")
    elif decision == "allow":
        emit("allow")
    else:
        emit("deny", "Denied via crab pet")


if __name__ == "__main__":
    main()
