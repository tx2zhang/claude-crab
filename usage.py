"""Local Claude usage stats, computed from Claude Code transcripts.

Every assistant reply in ~/.claude/projects/**/*.jsonl records its exact
token usage, so we can rebuild Claude's 5-hour session windows and weekly
totals without touching the network or any credentials.

There's no plan limit in the logs, so bars are relative to your own busiest
5-hour session / busiest week in the retained history.

Transcripts are append-only, so the scan is incremental: we remember how
far into each file we've read and only parse new bytes. The index persists
to disk so a fresh pet start doesn't re-read hundreds of MB.
"""

import json
import time
from datetime import datetime, timedelta
from pathlib import Path

PROJECTS = Path.home() / ".claude" / "projects"
BLOCK = 5 * 3600            # Claude's rolling session window
HISTORY_DAYS = 35           # Claude Code keeps ~30 days of transcripts
INDEX_VERSION = 1


def _ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


class UsageIndex:
    def __init__(self, cache_file):
        self.cache_file = Path(cache_file)
        # path -> {"off": bytes_read, "rows": [[ts, msg_key, tokens], ...]}
        self.files = {}
        self._load()

    # ---------- persistence ----------

    def _load(self):
        try:
            data = json.loads(self.cache_file.read_text())
            if data.get("v") == INDEX_VERSION:
                self.files = data.get("files", {})
        except Exception:
            self.files = {}

    def _save(self):
        try:
            tmp = self.cache_file.with_suffix(".tmp")
            tmp.write_text(json.dumps({"v": INDEX_VERSION, "files": self.files},
                                      separators=(",", ":")))
            tmp.replace(self.cache_file)
        except Exception:
            pass

    # ---------- scanning ----------

    def _scan(self, path, entry):
        """Parse bytes appended since the last scan. Partial trailing lines
        (a reply still being written) are left for next time."""
        with open(path, "rb") as f:
            f.seek(entry["off"])
            chunk = f.read()
        end = chunk.rfind(b"\n")
        if end < 0:
            return
        entry["off"] += end + 1
        for raw in chunk[:end].split(b"\n"):
            if b'"usage"' not in raw or b'"assistant"' not in raw:
                continue
            try:
                o = json.loads(raw)
            except Exception:
                continue
            if o.get("type") != "assistant":
                continue
            msg = o.get("message") or {}
            u = msg.get("usage") or {}
            ts = o.get("timestamp")
            if not u or not ts:
                continue
            tokens = ((u.get("input_tokens") or 0) + (u.get("output_tokens") or 0)
                      + (u.get("cache_creation_input_tokens") or 0))
            key = f"{msg.get('id')}|{o.get('requestId')}"
            try:
                entry["rows"].append([round(_ts(ts), 1), key, tokens])
            except Exception:
                continue

    def refresh(self):
        cutoff = time.time() - HISTORY_DAYS * 86400
        live = set()
        for path in PROJECTS.rglob("*.jsonl"):
            try:
                st = path.stat()
            except OSError:
                continue
            if st.st_mtime < cutoff:
                continue
            k = str(path)
            live.add(k)
            entry = self.files.get(k)
            if entry is None or st.st_size < entry["off"]:
                entry = self.files[k] = {"off": 0, "rows": []}   # new or rewritten
            if st.st_size > entry["off"]:
                try:
                    self._scan(path, entry)
                except OSError:
                    continue
            entry["rows"] = [r for r in entry["rows"] if r[0] >= cutoff]
        for k in list(self.files):
            if k not in live:
                del self.files[k]
        self._save()

    # ---------- stats ----------

    def stats(self, now=None):
        now = now or time.time()
        # Resumed chats copy earlier history into the new transcript, and one
        # reply is written as several lines — dedupe by message + request id.
        best = {}
        for entry in self.files.values():
            for ts, key, tok in entry["rows"]:
                prev = best.get(key)
                if prev is None or tok > prev[1]:
                    best[key] = (ts, tok)
        rows = sorted(best.values())

        blocks = []
        for ts, tok in rows:
            if not blocks or ts >= blocks[-1][0] + BLOCK:
                blocks.append([ts - ts % 3600, 0])     # window starts on the hour
            blocks[-1][1] += tok
        active = blocks[-1] if blocks and now < blocks[-1][0] + BLOCK else None
        peak_block = max((b[1] for b in blocks), default=0)

        weeks = [sum(t for ts, t in rows if now - (i + 1) * 7 * 86400 <= ts < now - i * 7 * 86400)
                 for i in range(4)]
        today0 = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0)
        days = []
        for i in range(6, -1, -1):
            d0 = (today0 - timedelta(days=i)).timestamp()
            d1 = d0 + 86400
            days.append(((today0 - timedelta(days=i)).strftime("%a")[0],
                         sum(t for ts, t in rows if d0 <= ts < d1)))

        return {
            "session": active[1] if active else 0,
            "session_resets_in": (active[0] + BLOCK - now) if active else None,
            "session_peak": peak_block,
            "week": weeks[0],
            "week_peak": max(weeks) if weeks else 0,
            "days": days,                      # [(letter, tokens)] oldest → today
            "replies_today": sum(1 for ts, _ in rows if ts >= today0.timestamp()),
        }


def fmt_tokens(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(int(n))


def fmt_duration(sec):
    sec = max(0, int(sec))
    h, m = sec // 3600, (sec % 3600) // 60
    return f"{h}h {m}m" if h else f"{m}m"
