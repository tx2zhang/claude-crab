"""Crab desktop pet for Claude Code status.

- Polls ~/.claude/pet/state/sessions/*.json for active Claude sessions.
- Renders one dark pill per session, vertically stacked above the crab.
- Standing crab when nothing is working, typing animation when any session
  is working, dangling legs while you drag the pet around.
- Speech bubble pops up when pending.json appears, with Yes / Always / No.
- Click crab/pill -> focus existing Claude window or launch one.
- Right-click for menu (open claude / clear sessions / autostart / quit).
"""

import ctypes
import json
import os
import random
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path

# Windows-only: hides the brief console flash from background PowerShell calls.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _enable_dpi_awareness():
    """Draw at the display's real resolution. Without this, Windows renders
    the pet at 96 DPI and bitmap-stretches it (2x on a 200% display), which
    made all text and art soft. Returns the UI scale factor (1.0 at 100%)."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)      # system DPI aware
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            return 1.0
    try:
        return ctypes.windll.user32.GetDpiForSystem() / 96.0
    except Exception:
        return 1.0


UI_SCALE = _enable_dpi_awareness()


def dp(v):
    """Design pixels → device pixels. Fonts given in points scale on their
    own; every raw pixel size/offset/padding goes through dp()."""
    return int(round(v * UI_SCALE))


CRAB_SCALE = max(1, int(round(5 * UI_SCALE)))   # integer, keeps pixel art crisp

try:
    import winsound  # Windows-only — used for the optional chime
except ImportError:
    winsound = None

from PIL import Image, ImageDraw, ImageFont, ImageTk

# Optional companions living next to pet.py: the vector crow character and
# the local usage stats behind the click-to-open usage panel.
try:
    import crow as crow_art
except Exception:
    crow_art = None
try:
    import usage as usage_stats
except Exception:
    usage_stats = None

ROOT = Path(os.path.expanduser("~/.claude/pet"))
STATE_DIR = ROOT / "state"
SESSIONS_DIR = STATE_DIR / "sessions"
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
LEGACY_STATUS_FILE = STATE_DIR / "status.json"
# Live-session registry the Claude Desktop app writes for EVERY running
# session — Code, Chat, and Cowork modes alike. Hooks don't fire reliably
# for Chat/Cowork, so we poll this dir to surface those sessions too and
# infer "working" from transcript mtime.
LIVE_SESSIONS_DIR = Path.home() / ".claude" / "sessions"
LIVE_PROJECTS_DIR = Path.home() / ".claude" / "projects"
PENDING_FILE = STATE_DIR / "pending.json"
RESPONSE_FILE = STATE_DIR / "response.json"
ALWAYS_FILE = STATE_DIR / "always_allow.json"
SETTINGS_FILE = STATE_DIR / "settings.json"
# User-assigned pill names (sid -> name), set via the pill's right-click
# Rename. These OVERRIDE the transcript-derived title — handy because Claude
# Desktop's reworded Recents title isn't written to the transcript file.
SESSION_NAMES_FILE = STATE_DIR / "session_names.json"

# CLI arg parsing:
#   pet.py                  → default crab
#   pet.py --dragon         → default dragon
#   pet.py <id>             → crab duplicate with instance ID <id>
#   pet.py --dragon <id>    → dragon duplicate with instance ID <id>
THEME = "crab"
INSTANCE_ID = "default"
CHARACTER_ARG = None   # --crow starts as the crow regardless of saved choice
for _arg in sys.argv[1:]:
    if _arg in ("--dragon", "-D"):
        THEME = "dragon"
    elif _arg in ("--crab", "-C"):
        THEME = "crab"
    elif _arg == "--crow":
        CHARACTER_ARG = "crow"
    elif not _arg.startswith("-"):
        INSTANCE_ID = _arg

# Position file: theme-specific for defaults, ID-specific for duplicates.
if INSTANCE_ID == "default":
    POSITION_FILE = STATE_DIR / ("position.json" if THEME == "crab"
                                  else f"position_{THEME}.json")
else:
    POSITION_FILE = STATE_DIR / f"position_{INSTANCE_ID}.json"


def load_settings():
    try:
        return json.loads(SETTINGS_FILE.read_text())
    except Exception:
        return {}


def save_settings(settings):
    try:
        SETTINGS_FILE.write_text(json.dumps(settings))
    except Exception:
        pass


# Mood-based emoji + text blurbs shown next to a working crab. The mood is
# inferred from Claude's actual transcript output (see detect_mood), which
# only ever returns one of these keys. The crab leans grumpy: low self
# esteem, easily annoyed. "thinking" is the neutral fallback bucket.
MOOD_EMOJI = {
    "happy":     ["😊", "✨", "💪", "🎯", "🙌", "🥳", "🔥", "⭐"],
    "confused":  ["🤔", "❓", "🧐", "😕", "🤷"],
    "thinking":  ["💭", "⚡", "🔍", "🧠", "⏳"],
    "angry":     ["😤", "😠", "😡", "💢", "😒"],
    "rage":      ["🤬", "💥", "😡", "🔥", "💢"],
    "depressed": ["😔", "😞", "💀", "😭", "🥺", "😩"],
}

MOOD_BLURBS = {
    "happy":     ["nailed it", "easy!", "let's go", "boom", "got it", "yesss"],
    "confused":  ["hmm...", "wait what", "huh?", "let me think", "weird",
                  "ok wait...", "that's odd"],
    "thinking":  ["thinking...", "processing", "hmm", "loading",
                  "one sec", "computing"],
    "angry":     ["ugh", "really?", "come on", "annoying",
                  "this is broken", "wtf code"],
    "rage":      ["WTF", "AAARGH", "DAMMIT", "I HATE THIS",
                  "WHYYY", "FFS", "ARE YOU KIDDING"],
    "depressed": ["why can't I solve this", "what is wrong with me",
                  "i'm stuck", "i give up", "send help",
                  "i'm bad at this", "i suck", "why am i like this"],
}


def detect_mood(transcript_path):
    """Heuristic mood inference from a session's recent assistant output."""
    if not transcript_path:
        return None
    try:
        p = Path(transcript_path)
        if not p.exists():
            return None
        size = p.stat().st_size
        with open(p, "rb") as f:
            if size > 20000:
                f.seek(-20000, 2)
            data = f.read().decode("utf-8", errors="replace")
        blob_parts = []
        for line in data.split("\n")[-40:]:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if not isinstance(obj, dict) or obj.get("type") != "assistant":
                continue
            msg = obj.get("message")
            if not isinstance(msg, dict):
                continue
            content = msg.get("content")
            if isinstance(content, list):
                for c in content:
                    if not isinstance(c, dict):
                        continue
                    if c.get("type") == "text":
                        blob_parts.append(c.get("text", "")[:600])
                    elif c.get("type") == "thinking":
                        blob_parts.append(c.get("thinking", "")[:600])
        text = " ".join(blob_parts).lower()
        if not text:
            return None

        def has(*words):
            # Word-boundary match so "ugh" doesn't fire on "enough"/"though",
            # "wait" doesn't fire on "await"/"waiting", etc. Multi-word
            # phrases (e.g. "what the hell") are matched as-is.
            for w in words:
                if re.search(r"\b" + re.escape(w) + r"\b", text):
                    return True
            return False

        # Order matters — strongest signals first.
        if has("ugh", "argh", "wtf", "what the hell", "frustrated",
               "frustrating"):
            return "rage"
        if has("damn", "broken", "horrible", "annoying"):
            return "angry"
        if has("stuck", "can't figure", "no idea", "lost here", "give up"):
            return "depressed"
        if has("hmm", "wait", "let me think", "not sure",
               "perhaps", "actually", "maybe", "weird"):
            return "confused"
        if has("great", "perfect", "excellent", "nice",
               "fixed", "works now", "done"):
            return "happy"
        return None  # no detectable sentiment — don't fire a fake emote
    except Exception:
        return None


def play_chime(kind="cheer"):
    """Soft system beep. Only on Windows, only if winsound is available."""
    if winsound is None:
        return
    try:
        flag = winsound.MB_ICONASTERISK if kind == "cheer" else winsound.MB_ICONEXCLAMATION
        winsound.MessageBeep(flag)
    except Exception:
        pass

TRANSPARENT_KEY = "#ff00ff"
TAG_BG = "#2b2b30"
TAG_FG = "#f0f0f0"

# Apple-style "frosted glass" look for the popup notifications (permission
# bubble, MCP reauth, toast, mood blurb): a cool translucent card with a
# hairline light edge, a bright top sheen, rounded corners, and real
# window translucency.
GLASS_FILL   = "#30333c"   # frosted dark slate (card body)
GLASS_BORDER = "#5f6675"   # soft light hairline edge
GLASS_SHEEN  = "#808799"   # brighter highlight along the top edge
GLASS_FG     = "#f3f4f6"   # primary text
GLASS_FG_DIM = "#a8aeba"   # secondary text
GLASS_RADIUS = dp(16)         # corner radius (px)
GLASS_ALPHA  = 0.90        # window translucency (standalone popups + pills)
# Crisp UI font for glass text. Tahoma hints far sharper than Segoe at small
# sizes on layered (transparent) windows, which only get grayscale AA.
GLASS_FONT   = "Tahoma"
GLASS_PAD    = dp(12)         # inset between the card edge and its content
PILL_PAD_X   = dp(12)         # pill card horizontal inset
PILL_PAD_Y   = dp(5)          # pill card vertical inset
PILL_RADIUS  = dp(11)         # pill corner radius (smaller than popups)


def _round_rect_points(x1, y1, x2, y2, r):
    """Corner points for a rounded rectangle, drawn via a smoothed polygon."""
    return [
        x1 + r, y1,  x2 - r, y1,  x2, y1,  x2, y1 + r,
        x2, y2 - r,  x2, y2,  x2 - r, y2,  x1 + r, y2,
        x1, y2,  x1, y2 - r,  x1, y1 + r,  x1, y1,
    ]

STATE_LOOKS = {
    "idle":       ("idle",         "#bbbbbb"),
    "ready":      ("ready",        "#7ecf6a"),
    "working":    ("working...",   "#f6bf3a"),
    "tool":       ("tool running", "#f6bf3a"),
    "finished":   ("done",         "#7ecf6a"),
    "attention":  ("needs you!",   "#ff7777"),
    "permission": ("approve?",     "#ffaa55"),
}

PALETTE = {
    "1": (217, 116, 90, 255),     # crab body coral
    "2": (30, 20, 18, 255),       # eyes
    "k": (55, 55, 65, 255),       # keyboard
    "K": (110, 110, 130, 255),    # keyboard lit key
    "z": (235, 235, 240, 255),    # Zzz (rendered text glyph color)
    # Confetti colors for the cheer animation
    "r": (255, 110, 110, 255),    # red
    "y": (255, 215, 80, 255),     # yellow
    "B": (100, 180, 255, 255),    # confetti blue
    "G": (130, 220, 130, 255),    # confetti green (capital, dragon uses lowercase)
    "p": (200, 130, 230, 255),    # purple
    # Sleep cap palette (pastel)
    "b": (174, 218, 247, 255),    # pastel baby blue (sleep cap body)
    "W": (250, 250, 255, 255),    # soft white (pom-pom & trim)
    # Dragon palette
    "g": (95, 180, 105, 255),     # dragon body green
    "h": (210, 175, 80, 255),     # dragon horn yellow-brown
    "w": (60, 135, 85, 255),      # dragon wing (darker green)
    # Beanbag shading (for the sleeping cushion)
    "L": (220, 238, 252, 255),    # light pastel highlight (cushion top edges)
    "d": (118, 170, 215, 255),    # darker shadow (cushion base + dimple)
}

# Find a usable TrueType font on Windows for rendering Z characters.
_FONT_CANDIDATES = [
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
]
FONT_PATH = next((p for p in _FONT_CANDIDATES if os.path.exists(p)), None)


# Standing crab — narrower side claws, small 1-pixel eye dots, 4 thin legs.
STANDING = [
    "................",  # 0
    "................",  # 1
    "................",  # 2
    "................",  # 3
    "....11111111....",  # 4
    "...1111111111...",  # 5
    ".11112111121111.",  # 6  claws + body + 1-pixel eye dots (cols 5, 10)
    ".11111111111111.",  # 7  claws + body
    "...1111111111...",  # 8
    "....11111111....",  # 9
    "....1.1..1.1....",  # 10  4 thin legs (cols 4, 6, 9, 11)
    "....1.1..1.1....",  # 11
    "....1.1..1.1....",  # 12
    "................",  # 13
]

_BODY = STANDING[:10]

# Dangle legs match the 3-row standing legs. The top row stays anchored to
# the body; the bottom two rows swing left/right like a pendulum.
_LEGS_DANGLE_C = [
    "....1.1..1.1....",
    "....1.1..1.1....",
    "....1.1..1.1....",
    "................",
]
_LEGS_DANGLE_L = [
    "....1.1..1.1....",
    "...1.1..1.1.....",
    "...1.1..1.1.....",
    "................",
]
_LEGS_DANGLE_R = [
    "....1.1..1.1....",
    ".....1.1..1.1...",
    ".....1.1..1.1...",
    "................",
]

DANGLE_FRAMES = [
    _BODY + _LEGS_DANGLE_C,
    _BODY + _LEGS_DANGLE_L,
    _BODY + _LEGS_DANGLE_C,
    _BODY + _LEGS_DANGLE_R,
]

# Typing — body and keyboard hold absolutely still. Only the legs cycle
# (one lifted at a time) and the keyboard's lit key follows the tapping leg.
_TYPING_BODY = [
    "................",  # 0
    "................",  # 1
    "................",  # 2
    "....11111111....",  # 3 body top
    "...1111111111...",  # 4
    ".11112111121111.",  # 5 claws + 1-pixel eye dots
    ".11111111111111.",  # 6 claws + body
    "...1111111111...",  # 7
    "....11111111....",  # 8 body bottom
]

# Three leg rows; the lifted leg is missing from the BOTTOM row only,
# so the leg looks shorter than its neighbors — same vibe as a loading dot.
_LEGS_LIFT_1 = ["....1.1..1.1....", "....1.1..1.1....", "......1..1.1...."]
_LEGS_LIFT_2 = ["....1.1..1.1....", "....1.1..1.1....", "....1....1.1...."]
_LEGS_LIFT_3 = ["....1.1..1.1....", "....1.1..1.1....", "....1.1....1...."]
_LEGS_LIFT_4 = ["....1.1..1.1....", "....1.1..1.1....", "....1.1..1......"]

# Lit key is the PREVIOUSLY-tapped key — when a leg lifts (about to come
# down) the key under the *previous* leg is the one that was just pressed
# and is still glowing. So we shift by one frame: lit column tracks the
# leg from the previous frame, not the current lifted leg.
_KBD_LIT_1 = [".kkkkkkkkkkKkkk.", ".kkkkkkkkkkkkkk."]   # lit col 11 (prev: leg 4)
_KBD_LIT_2 = [".kkkKkkkkkkkkkk.", ".kkkkkkkkkkkkkk."]   # lit col 4  (prev: leg 1)
_KBD_LIT_3 = [".kkkkkKkkkkkkkk.", ".kkkkkkkkkkkkkk."]   # lit col 6  (prev: leg 2)
_KBD_LIT_4 = [".kkkkkkkkKkkkkk.", ".kkkkkkkkkkkkkk."]   # lit col 9  (prev: leg 3)

TYPING_FRAMES = [
    _TYPING_BODY + _LEGS_LIFT_1 + _KBD_LIT_1,
    _TYPING_BODY + _LEGS_LIFT_2 + _KBD_LIT_2,
    _TYPING_BODY + _LEGS_LIFT_3 + _KBD_LIT_3,
    _TYPING_BODY + _LEGS_LIFT_4 + _KBD_LIT_4,
]


# Cheer — happy closed eyes (clean horizontal lines at the standard eye row,
# so the eyes look like they smile-closed in place) plus falling confetti.
# Plays for ~2 seconds after a session transitions to "finished".
_CHEER_BODY = [
    "....11111111....",  # 4 body top
    "...1111111111...",  # 5
    ".11112211221111.",  # 6 closed eye lines at the standard eye row
    ".11111111111111.",  # 7 plain body underneath
    "...1111111111...",  # 8
    "....11111111....",  # 9 body bottom
    "....1.1..1.1....",  # 10 legs
    "....1.1..1.1....",  # 11
    "....1.1..1.1....",  # 12
    "................",  # 13
]

CHEER_FRAMES = [
    [
        "r..y...B..G..p..",
        "...r...y....B...",
        "................",
        "................",
    ] + _CHEER_BODY,
    [
        "................",
        "r..y...B..G..p..",
        "...r...y....B...",
        "................",
    ] + _CHEER_BODY,
    [
        "................",
        "................",
        "r..y...B..G..p..",
        "...r...y....B...",
    ] + _CHEER_BODY,
    [
        "................",
        "................",
        "................",
        "r..y...B..G..p..",
    ] + _CHEER_BODY,
]


# Sleeping — classic droopy nightcap: pastel baby-blue body tilted up-right
# with a white pom-pom hanging off the lower-left, white trim band as the brim
# around the head. Eyes closed; Zzz floats up the right side, clear of the cap.
SLEEPING_BODY = [
    ".....bbbb.......",  # 0 cap peak (top-right of the head)
    "..bbbbbbbb......",  # 1 cap widens going down-left
    "WWbbbbbbbbb.....",  # 2 pom-pom (top row) + cap body continues
    ".w.WWWWWWWWW....",  # 3 small pom-pom tip ('w' = smaller dot) + trim band
    "....11111111....",  # 4 body top
    "...1111111111...",  # 5
    ".11111111111111.",  # 6 closed eye top
    ".11112211221111.",  # 7 closed eyelid line
    "...1111111111...",  # 8
    "..Lb11111111bL..",  # 9 body bottom — cushion flanges hug body sides
    ".LbbbbbbbbbbbbL.",  # 10 cushion top — light highlight outer edges
    ".LbbbbddddbbbbL.",  # 11 widest + press-dimple under the crab's center
    ".bbbbbbbbbbbbbb.",  # 12 cushion mid — main color
    "..dddddddddddd..",  # 13 shadow base — narrower, darker underside
]

# Z positions per sleeping frame. Cap fills cols 0-11 at the top, so Zzz
# floats up the far-right column (cols 12+).
SLEEPING_Z_FRAMES = [
    [(62, 8, 12)],                        # 0: Z near body, right of cap
    [(66, -2, 11), (58, 18, 11)],         # 1: prev Z floats up + new Z low
    [(62, 12, 11)],                       # 2: mid Z
    [],                                    # 3: rest beat
]


# ===================== DRAGON THEME =====================
# Same animation system, dragon sprites. Triggered by `pet.py --dragon`.

DRAGON_STANDING = [
    "................",  # 0
    "....h......h....",  # 1 horn tips
    "....h......h....",  # 2 horn bodies
    "...gggggggggg...",  # 3 head top
    "..gggggggggggg..",  # 4 head wider
    "..gg22gggg22gg..",  # 5 eyes (cols 4-5, 10-11)
    "..gggggggggggg..",  # 6 head bottom
    "..wggggggggggw..",  # 7 wing tips + body
    "..wggggggggggw..",  # 8 wing tips + body
    "...gggggggggg...",  # 9 body bottom
    "....g.g..g.g....",  # 10 legs (4 thin)
    "....g.g..g.g....",  # 11
    "....g.g..g.g....",  # 12
    "................",  # 13
]

_DRAGON_BODY = DRAGON_STANDING[:10]

_DRAGON_LEGS_DANGLE_C = [
    "....g.g..g.g....",
    "....g.g..g.g....",
    "....g.g..g.g....",
    "................",
]
_DRAGON_LEGS_DANGLE_L = [
    "....g.g..g.g....",
    "...g.g..g.g.....",
    "...g.g..g.g.....",
    "................",
]
_DRAGON_LEGS_DANGLE_R = [
    "....g.g..g.g....",
    ".....g.g..g.g...",
    ".....g.g..g.g...",
    "................",
]

DRAGON_DANGLE_FRAMES = [
    _DRAGON_BODY + _DRAGON_LEGS_DANGLE_C,
    _DRAGON_BODY + _DRAGON_LEGS_DANGLE_L,
    _DRAGON_BODY + _DRAGON_LEGS_DANGLE_C,
    _DRAGON_BODY + _DRAGON_LEGS_DANGLE_R,
]

# Typing: compressed body to fit legs + keyboard.
_DRAGON_TYPING_BODY = [
    "................",  # 0
    "................",  # 1
    "................",  # 2
    "....h......h....",  # 3 horn tips
    "....h......h....",  # 4 horn bodies
    "..gggggggggggg..",  # 5 head top
    "..gg22gggg22gg..",  # 6 eyes
    "..wggggggggggw..",  # 7 wings + body
    "...gggggggggg...",  # 8 body bottom
]

_DRAGON_TYPING_LEGS_1 = ["....g.g..g.g....", "....g.g..g.g....", "......g..g.g...."]
_DRAGON_TYPING_LEGS_2 = ["....g.g..g.g....", "....g.g..g.g....", "....g....g.g...."]
_DRAGON_TYPING_LEGS_3 = ["....g.g..g.g....", "....g.g..g.g....", "....g.g....g...."]
_DRAGON_TYPING_LEGS_4 = ["....g.g..g.g....", "....g.g..g.g....", "....g.g..g......"]

DRAGON_TYPING_FRAMES = [
    _DRAGON_TYPING_BODY + _DRAGON_TYPING_LEGS_1 + _KBD_LIT_1,
    _DRAGON_TYPING_BODY + _DRAGON_TYPING_LEGS_2 + _KBD_LIT_2,
    _DRAGON_TYPING_BODY + _DRAGON_TYPING_LEGS_3 + _KBD_LIT_3,
    _DRAGON_TYPING_BODY + _DRAGON_TYPING_LEGS_4 + _KBD_LIT_4,
]

# Cheer: closed-eye lines + confetti.
_DRAGON_CHEER_BODY = [
    "....h......h....",  # 4 horn tips visible
    "....h......h....",  # 5 horn bodies
    "..gggggggggggg..",  # 6 head top
    "..gg22gggg22gg..",  # 7 closed eye lines
    "..wggggggggggw..",  # 8 wings
    "..wggggggggggw..",  # 9 wings
    "...gggggggggg...",  # 10 body
    "....g.g..g.g....",  # 11-12 legs
    "....g.g..g.g....",
    "................",  # 13
]

DRAGON_CHEER_FRAMES = [
    [
        "r..y...B..G..p..",
        "...r...y....B...",
        "................",
        "................",
    ] + _DRAGON_CHEER_BODY,
    [
        "................",
        "r..y...B..G..p..",
        "...r...y....B...",
        "................",
    ] + _DRAGON_CHEER_BODY,
    [
        "................",
        "................",
        "r..y...B..G..p..",
        "...r...y....B...",
    ] + _DRAGON_CHEER_BODY,
    [
        "................",
        "................",
        "................",
        "r..y...B..G..p..",
    ] + _DRAGON_CHEER_BODY,
]

# Sleeping: nightcap covers the horns; eyes closed (drooped one row).
DRAGON_SLEEPING_BODY = [
    ".....bbbb.......",  # 0 cap peak
    "..bbbbbbbb......",  # 1 cap widens
    "WWbbbbbbbbb.....",  # 2 pom-pom + cap (covers horns)
    ".w.WWWWWWWWW....",  # 3 small pom-pom tip + trim band
    "..gggggggggggg..",  # 4 head top
    "..gggggggggggg..",  # 5 head (no top eye row — closed)
    "..gg22gggg22gg..",  # 6 closed eyelid lines (drooped)
    "..gggggggggggg..",  # 7 head bottom
    "..wggggggggggw..",  # 8 wings
    "..wggggggggggw..",  # 9 wings
    "...gggggggggg...",  # 10 body
    "....g.g..g.g....",  # 11 legs (1 row, tucked)
    "....g.g..g.g....",  # 12
    "................",  # 13
]

# Theme dispatch — overwrite the crab sprites with dragon ones if active.
if THEME == "dragon":
    STANDING = DRAGON_STANDING
    DANGLE_FRAMES = DRAGON_DANGLE_FRAMES
    TYPING_FRAMES = DRAGON_TYPING_FRAMES
    CHEER_FRAMES = DRAGON_CHEER_FRAMES
    SLEEPING_BODY = DRAGON_SLEEPING_BODY


def _shift_up(pixels, n):
    """Shift sprite up by n rows, padding the bottom with blank rows. Used
    to build the hop animation by translating the standing sprite vertically."""
    blank = "." * len(pixels[0])
    return list(pixels[n:]) + [blank] * n


# Hop frames — body lifts 1 → 2 → 1 → 0 rows over ~400ms. Plays when any
# session transitions to "working" (the crab "hops to attention").
HOP_FRAMES = [
    _shift_up(STANDING, 1),
    _shift_up(STANDING, 2),
    _shift_up(STANDING, 1),
    STANDING,
]


def crab_image(pixels, scale=None, zs=None):
    scale = scale or CRAB_SCALE
    h = len(pixels)
    w = max(len(r) for r in pixels)
    img = Image.new("RGBA", (w * scale, h * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    for y, row in enumerate(pixels):
        for x, ch in enumerate(row):
            if ch == "w":
                # Small pom-pom tip: a slightly-bigger centered dot drawn at the
                # LEFT EDGE of this cell, so it visually sits between this cell
                # and the previous one (centered under a 2-cell pom-pom top).
                size = max(2, scale - 1)
                half = size // 2
                cx = x * scale
                cy = y * scale + scale // 2
                draw.rectangle(
                    [cx - half, cy - half,
                     cx + size - half - 1, cy + size - half - 1],
                    fill=PALETTE["W"],
                )
            elif ch in PALETTE:
                draw.rectangle(
                    [x * scale, y * scale, (x + 1) * scale - 1, (y + 1) * scale - 1],
                    fill=PALETTE[ch],
                )
    if zs and FONT_PATH:
        k = scale / 5.0
        for x_px, y_px, font_size in zs:
            try:
                font = ImageFont.truetype(FONT_PATH, max(1, int(round(font_size * k))))
                draw.text((x_px * k, y_px * k), "Z", fill=PALETTE["z"], font=font)
            except Exception:
                pass
    return img


def _is_garbage_label(text):
    """True if a label was clearly extracted from a non-prompt: file drop,
    Claude Code caveat, system tag, or context-continuation banner. We drop
    these so they don't pollute the pill stack with junk."""
    if not text:
        return False
    s = text.strip()
    if not s:
        return True
    if s.startswith("@"):
        return True
    if s.startswith(("Caveat:", "<", "[Request", "This session")):
        return True
    return False


def _short_label(text, max_words=6, max_chars=42):
    """Trim a title to ~6 words / 42 chars to fit the pill."""
    if not text:
        return None
    text = re.sub(r"<[^>]+>", " ", text)
    words = [w for w in text.split() if w and any(c.isalnum() for c in w)]
    if not words:
        return None
    return " ".join(words[:max_words])[:max_chars]


def _pid_alive(pid):
    """Fast in-process check (no subprocess) whether a PID is running, used
    to tell a live Claude session from a stale registry file left behind by
    a crash. OpenProcess + GetExitCodeProcess == STILL_ACTIVE."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return False
    try:
        code = ctypes.c_ulong()
        ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
        return bool(ok) and code.value == STILL_ACTIVE
    finally:
        k32.CloseHandle(h)


def _derive_transcript_path(cwd, sid):
    """Map a Claude Desktop session's cwd + sessionId to its transcript
    JSONL inside ~/.claude/projects/. Encoding rule (from observation):
    each of ':', '\\', '/', '.' becomes '-'.

      C:\\Users\\Tian\\.claude  →  C--Users-Tian--claude
      C:\\Users\\Tian           →  C--Users-Tian
    """
    if not cwd or not sid:
        return None
    encoded = "".join("-" if ch in ":\\/." else ch for ch in cwd)
    return LIVE_PROJECTS_DIR / encoded / f"{sid}.jsonl"


def _user_text(obj):
    """Pull the typed text out of a 'user' transcript entry, or None if it
    isn't a plain typed prompt (tool results, attachments-only, etc.)."""
    msg = obj.get("message")
    if not isinstance(msg, dict):
        return None
    content = msg.get("content")
    if isinstance(content, str):
        return content.strip() or None
    if isinstance(content, list):
        parts = [
            c.get("text", "") for c in content
            if isinstance(c, dict) and c.get("type") == "text"
        ]
        joined = " ".join(p for p in parts if p).strip()
        return joined or None
    return None


def extract_label_from_transcript(path):
    """Best human-readable label for a session, in priority order:
      1. custom-title  — the user's explicit rename
      2. ai-title      — Claude Desktop's auto-generated title
      3. first user prompt — rough placeholder until a title exists, so a
         brand-new chat reads like "turn this into light" instead of a
         generic "new chat". Garbage (file drops, Caveats, slash commands,
         system tags) is filtered out.
    """
    if not path:
        return None
    try:
        custom = None
        ai = None
        first_user = None
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
                        custom = title
                elif t == "ai-title":
                    title = obj.get("aiTitle")
                    if title:
                        ai = title
                elif t == "user" and first_user is None:
                    txt = _user_text(obj)
                    # Skip junk: file drops (@...), Caveats / system tags,
                    # slash commands. _is_garbage_label catches most; also
                    # skip anything starting with '/' (a slash command).
                    if txt and not txt.startswith("/") and not _is_garbage_label(txt):
                        first_user = txt
        return (_short_label(custom) or _short_label(ai)
                or _short_label(first_user))
    except Exception:
        return None


EMOJI_FONT_PATH = "C:/Windows/Fonts/seguiemj.ttf"


def _no_aa_fringing(img):
    """Snap the alpha channel to binary (0 or 255) so Tk can't composite
    partially-transparent edge pixels with the magenta transparent-color key.
    Result: clean opaque emoji with hard edges, no halo of any color."""
    img = img.convert("RGBA")
    r, g, b, a = img.split()
    new_a = a.point(lambda v: 255 if v > 110 else 0)
    return Image.merge("RGBA", (r, g, b, new_a))


def render_emoji(emoji, size=None):
    """Render an emoji on a TRANSPARENT background using Segoe UI Emoji's
    color glyphs (PIL's `embedded_color=True`). The emoji floats with no
    dark chip behind it. Returns a PIL Image, or None if the font is missing.
    """
    if not os.path.exists(EMOJI_FONT_PATH):
        return None
    size = size or dp(22)
    pad_x, pad_y = dp(4), dp(2)
    w = size + pad_x * 2 + dp(6)
    h = size + pad_y * 2 + dp(4)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))  # fully transparent
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype(EMOJI_FONT_PATH, size)
    except Exception:
        return None
    try:
        draw.text((pad_x, pad_y), emoji, font=font, embedded_color=True)
    except (TypeError, ValueError):
        # Older Pillow without embedded_color support — fall back to plain text.
        draw.text((pad_x, pad_y), emoji, fill=(240, 240, 240, 255), font=font)
    return _no_aa_fringing(img)


def get_work_area():
    """Return (left, top, right, bottom) of the primary monitor's work area
    (excludes the taskbar). Returns None on failure (caller falls back to full screen)."""
    try:
        from ctypes.wintypes import RECT
        SPI_GETWORKAREA = 0x0030
        rect = RECT()
        ok = ctypes.windll.user32.SystemParametersInfoW(
            SPI_GETWORKAREA, 0, ctypes.byref(rect), 0
        )
        if ok:
            return (rect.left, rect.top, rect.right, rect.bottom)
    except Exception:
        pass
    return None


def find_claude_desktop_launcher():
    """Locate how to launch Claude Desktop on this machine.

    Returns ('appx', appid) for MSIX/AppX installs, ('exe', path) for plain
    installs, or None if neither is found.
    """
    # AppX (Claude Desktop ships as an MSIX package on Windows) — discover the
    # AppID via Get-StartApps so we don't have to hardcode the publisher hash.
    try:
        result = subprocess.run(
            [
                "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                "(Get-StartApps | Where-Object {$_.Name -eq 'Claude' -and "
                "$_.AppID -like '*!*' -and $_.AppID -notlike '*Crab*'} | "
                "Select-Object -First 1).AppID",
            ],
            capture_output=True, text=True, timeout=10,
            creationflags=_NO_WINDOW,
        )
        app_id = (result.stdout or "").strip()
        if app_id and "!" in app_id:
            return ("appx", app_id)
    except Exception:
        pass

    # Plain-exe fallback for non-AppX installs.
    candidates = [
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Claude\Claude.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\AnthropicClaude\Claude.exe"),
    ]
    for path in candidates:
        if os.path.exists(path):
            return ("exe", path)
    return None


def find_claude_window():
    user32 = ctypes.windll.user32
    EnumWindowsProc = ctypes.WINFUNCTYPE(
        ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p
    )
    hits = []

    def cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value or ""
        if "claude" in title.lower():
            hits.append((hwnd, title))
        return True

    user32.EnumWindows(EnumWindowsProc(cb), 0)
    hits.sort(key=lambda t: ("claude code" not in t[1].lower(), len(t[1])))
    return hits[0][0] if hits else None


def focus_or_launch_claude(launcher=None):
    """Bring an existing Claude window to front, or launch Claude Desktop.

    `launcher` is the result of find_claude_desktop_launcher(). When the AppX
    launcher is known we go straight through it — the AppX runtime handles
    both fresh launches and focusing an already-running instance.
    """
    if launcher and launcher[0] == "appx":
        try:
            subprocess.Popen(
                ["explorer.exe", f"shell:AppsFolder\\{launcher[1]}"],
                shell=False,
            )
            return
        except Exception:
            pass

    # Non-AppX path: try to focus an existing window first.
    hwnd = find_claude_window()
    if hwnd:
        user32 = ctypes.windll.user32
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        return

    if launcher and launcher[0] == "exe":
        try:
            subprocess.Popen([launcher[1]], shell=False)
            return
        except Exception:
            pass

    # Last resort: terminal CLI.
    for cmd in (["wt.exe", "claude"], ["cmd.exe", "/k", "claude"]):
        try:
            subprocess.Popen(cmd, shell=False)
            return
        except FileNotFoundError:
            continue


def startup_shortcut_path():
    appdata = os.environ.get("APPDATA", "")
    if not appdata:
        return None
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "ClaudeCrab.lnk"


def install_autostart():
    sc_path = startup_shortcut_path()
    if not sc_path:
        return False
    vbs = ROOT / "start-crab.vbs"
    icon_clause = (
        f"$s.IconLocation='{ICON_PATH},0';" if ICON_PATH.exists() else ""
    )
    ps_script = (
        "$w=New-Object -ComObject WScript.Shell;"
        f"$s=$w.CreateShortcut('{sc_path}');"
        "$s.TargetPath='wscript.exe';"
        f"$s.Arguments='\"{vbs}\"';"
        f"$s.WorkingDirectory='{ROOT}';"
        f"{icon_clause}"
        "$s.Save()"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            check=True, capture_output=True,
            creationflags=_NO_WINDOW,
        )
        return True
    except Exception:
        return False


def uninstall_autostart():
    sc_path = startup_shortcut_path()
    if sc_path and sc_path.exists():
        try:
            sc_path.unlink()
            return True
        except Exception:
            return False
    return True


def autostart_installed():
    sc_path = startup_shortcut_path()
    return bool(sc_path and sc_path.exists())


ICON_PATH = STATE_DIR / f"{THEME}.ico"


def generate_icon():
    """Render the sleeping crab as a multi-resolution .ico file.

    Used for the Start Menu / autostart / taskbar icon so it actually looks
    like a crab instead of the default Python logo.
    """
    try:
        sprite = crab_image(SLEEPING_BODY, scale=16, zs=None)
        # Square canvas (ICO format expects square images for proper scaling)
        side = max(sprite.width, sprite.height)
        square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        ox = (side - sprite.width) // 2
        oy = (side - sprite.height) // 2
        square.paste(sprite, (ox, oy))
        square.save(
            str(ICON_PATH),
            format="ICO",
            sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
                   (128, 128), (256, 256)],
        )
        return True
    except Exception:
        return False


def update_shortcut_icons():
    """Point existing crab shortcuts at the generated crab.ico."""
    if not ICON_PATH.exists():
        return
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return
    candidates = [
        Path(appdata) / "Microsoft/Windows/Start Menu/Programs/Startup/ClaudeCrab.lnk",
        Path(appdata) / "Microsoft/Windows/Start Menu/Programs/Claude Crab.lnk",
    ]
    for sc in candidates:
        if not sc.exists():
            continue
        ps_script = (
            "$w=New-Object -ComObject WScript.Shell;"
            f"$s=$w.CreateShortcut('{sc}');"
            f"$s.IconLocation='{ICON_PATH},0';"
            "$s.Save()"
        )
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_script],
                check=True, capture_output=True, timeout=10,
                creationflags=_NO_WINDOW,
            )
        except Exception:
            pass


class CrabPet:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("crab")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        try:
            self.root.attributes("-transparentcolor", TRANSPARENT_KEY)
        except tk.TclError:
            pass
        self.root.configure(bg=TRANSPARENT_KEY)

        # User preferences (chime, character, etc.).
        self.settings = load_settings()

        # Which character this instance shows: the base theme (crab/dragon)
        # or the vector crow. Saved per instance so a duplicate can differ.
        self._char_key = f"character:{THEME}:{INSTANCE_ID}"
        self.character = CHARACTER_ARG or self.settings.get(self._char_key) or THEME
        if self.character == "crow" and crow_art is None:
            self.character = THEME
        self._load_character(self.character)

        self._anim_after = None
        self._anim_frames = None
        self._anim_interval = 200
        self._anim_idx = 0
        self._current_anim = None

        self.frame = tk.Frame(self.root, bg=TRANSPARENT_KEY)
        self.frame.pack()

        # Pill stack lives in its OWN translucent window floating above the
        # crab, so the pills can be see-through glass while the crab stays
        # fully solid (window alpha is per-window). Positioned each tick.
        self.pill_window = tk.Toplevel(self.root)
        self.pill_window.overrideredirect(True)
        self.pill_window.attributes("-topmost", True)
        try:
            self.pill_window.attributes("-transparentcolor", TRANSPARENT_KEY)
            self.pill_window.attributes("-alpha", GLASS_ALPHA)
        except tk.TclError:
            pass
        self.pill_window.configure(bg=TRANSPARENT_KEY)
        self.pill_frame = tk.Frame(self.pill_window, bg=TRANSPARENT_KEY)
        self.pill_frame.pack()
        self.pill_window.withdraw()
        self._pill_window_visible = False
        self.pills = {}      # label -> {frame, label, sids, state}
        self.sessions = {}   # session_id -> {state, ts, label, transcript}

        # Crab sprite.
        self.crab_label = tk.Label(
            self.frame,
            image=self.standing_tk,
            bg=TRANSPARENT_KEY,
            bd=0,
            highlightthickness=0,
            cursor="hand2",
        )
        self.crab_label.pack()

        # Speech bubble (built but unpacked until needed).
        self._build_bubble()
        self.bubble_visible = False

        for w in (self.crab_label,):
            w.bind("<ButtonPress-1>", self.on_press)
            w.bind("<B1-Motion>", self.on_motion)
            w.bind("<ButtonRelease-1>", self.on_release)
            w.bind("<Button-3>", self.on_right_click)

        self._press_x = self._press_y = 0
        self._win_x = self._win_y = 0
        self._dragged = False

        self.pending_signature = None

        # Sessions Tian dismissed via the × button.
        # Map sid -> ts at moment of dismissal; if file ts increases, undismiss.
        self.dismissed = {}

        # Throttle for re-extracting labels from transcript files.
        # sid -> last time we re-read the JSONL. Refresh at most every 5s.
        self._label_check_ts = {}

        # User-assigned names (sid -> name) from the pill Rename action.
        self.session_names = self._load_session_names()
        self._rename_window = None

        # Floating "delete this pill" button shared by every pill — appears on
        # hover, hides on leave (with a small grace period so moving the mouse
        # from the pill to the button itself doesn't make it vanish).
        self._build_delete_button()
        self._delete_target = None
        self._delete_visible = False
        self._delete_hide_after = None

        # State transition tracking for cheer + hop animations.
        self._prev_states = {}     # sid -> last seen state
        self._cheer_until = 0.0    # epoch time when cheer animation ends
        self._hop_until = 0.0      # epoch time when hop animation ends

        # Cached Claude Desktop launcher info; discovered lazily so init is fast.
        self._claude_launcher_cache = None
        self._claude_launcher_discovered = False


        # Mood emote — floating Toplevel positioned to the right of the crab,
        # so it doesn't push the pill stack around.
        self._emote_visible = False
        self._emote_hide_at = 0.0
        self._next_mood_check = 0.0
        self._emoji_cache = {}  # emoji char -> ImageTk.PhotoImage of color chip
        self._build_emote_window()

        # Shake-to-clear: track recent drag positions to detect rapid wiggles.
        self._shake_pts = []
        self._shake_cooldown_until = 0.0
        # Auto wake-and-shake: after 10 min with nothing working, crab gets
        # up, shakes itself off, and dismisses every ready/idle pill.
        self._last_activity_ts = time.time()
        self._auto_clean_cooldown_until = 0.0
        self._shake_anim_active = False
        # Per-session "active grace": once a session has been seen actively
        # writing to its transcript, count it as still-working for a bit
        # longer so Claude's thinking pauses (and tool-call gaps) don't
        # drop the crab back to sleeping mid-task.
        self._inferred_active_until = {}
        # Last seen transcript size per session — we only treat a session
        # as "working" when the file actually GREW, not when mtime got
        # bumped by a viewer/click. Initialized lazily on first sighting.
        self._last_size = {}

        # Track previous "pending" state for chime trigger on permission popup.
        self._prev_pending = False

        # MCP reauth bubble — pops when ~/.claude/mcp-needs-auth-cache.json
        # gets a new entry / a timestamp bump for an existing one.
        self._mcp_auth_file = Path.home() / ".claude" / "mcp-needs-auth-cache.json"
        self._mcp_seen_ts = self._snapshot_mcp_cache()
        self._mcp_visible = False
        self._mcp_current_server = None
        self._build_mcp_bubble()

        # Usage panel (click the pet). Stats come from local transcripts;
        # warm the index in the background so the first click is instant.
        self._usage_window = self._usage_canvas = None
        self._usage_visible = False
        self._usage_opened_at = self._usage_closed_at = 0.0
        self._usage_tick_after = None
        self._usage_stats = None
        self._usage_index = None
        self._usage_lock = threading.Lock()
        if usage_stats is not None:
            threading.Thread(target=self._usage_refresh_worker, daemon=True).start()

        self.root.update_idletasks()
        self.load_position()

        # React the moment something covers / uncovers the crab — catches the
        # taskbar pop-up faster than the periodic loop.
        self.root.bind("<Visibility>", lambda _e: self._force_topmost())

        self.poll()
        self._topmost_loop()

    # ---------- frosted-glass helpers ----------

    def _draw_glass(self, canvas, w, h, radius=GLASS_RADIUS, fill=GLASS_FILL):
        """Paint the frosted-glass card (rounded fill + hairline edge + top
        sheen) onto a canvas, tagged "glass" and tucked behind the content."""
        canvas.delete("glass")
        r = radius
        canvas.create_polygon(
            _round_rect_points(1, 1, w - 1, h - 1, r), smooth=True,
            fill=fill, outline=GLASS_BORDER, width=1, tags="glass",
        )
        # Bright sheen just inside the top edge — the "glass" catch-light.
        canvas.create_line(r, dp(2), w - r, dp(2), fill=GLASS_SHEEN, width=1, tags="glass")
        canvas.tag_lower("glass")

    def _glass_wrap(self, parent):
        """Create a glass canvas + a content frame floating on it. Returns
        (canvas, content). Call _glass_refit after filling the content."""
        canvas = tk.Canvas(parent, bg=TRANSPARENT_KEY, highlightthickness=0, bd=0)
        content = tk.Frame(canvas, bg=GLASS_FILL)
        canvas._content_item = canvas.create_window(
            GLASS_PAD, GLASS_PAD, anchor="nw", window=content)
        return canvas, content

    def _glass_refit(self, canvas, content):
        """Resize the canvas to the content and repaint the glass card."""
        content.update_idletasks()
        w = content.winfo_reqwidth() + GLASS_PAD * 2
        h = content.winfo_reqheight() + GLASS_PAD * 2
        canvas.configure(width=w, height=h)
        canvas.coords(canvas._content_item, GLASS_PAD, GLASS_PAD)
        self._draw_glass(canvas, w, h)

    def _glass_button(self, parent, text, fill, active, command):
        """A flat, Apple-ish filled button that sits on the glass."""
        return tk.Button(
            parent, text=text, command=command,
            bg=fill, fg="white", activebackground=active, activeforeground="white",
            relief="flat", bd=0, font=("Segoe UI", 9), cursor="hand2",
            padx=dp(14), pady=dp(5),
        )

    # ---------- speech bubble ----------

    def _build_bubble(self):
        self.bubble_frame = tk.Frame(self.frame, bg=TRANSPARENT_KEY)
        self.bubble_canvas, self.bubble_body = self._glass_wrap(self.bubble_frame)
        self.bubble_canvas.pack()

        self.bubble_title = tk.Label(
            self.bubble_body, text="allow ?",
            bg=GLASS_FILL, fg=GLASS_FG, font=("Segoe UI", 10, "bold"),
        )
        self.bubble_title.pack(anchor="w")

        self.bubble_detail = tk.Label(
            self.bubble_body, text="",
            bg=GLASS_FILL, fg=GLASS_FG_DIM, font=("Consolas", 8),
            wraplength=dp(190), justify="left",
        )
        self.bubble_detail.pack(anchor="w", pady=(dp(3), 0))

        btn_row = tk.Frame(self.bubble_body, bg=GLASS_FILL)
        btn_row.pack(pady=(dp(10), 0))
        self._glass_button(btn_row, "Yes", "#30d158", "#43dd68",
                           lambda: self.respond("allow")).pack(side="left", padx=(0, dp(6)))
        self._glass_button(btn_row, "Always", "#0a84ff", "#2a97ff",
                           lambda: self.respond("always")).pack(side="left", padx=(0, dp(6)))
        self._glass_button(btn_row, "No", "#48484a", "#5a5a5c",
                           lambda: self.respond("deny")).pack(side="left")

        self._glass_refit(self.bubble_canvas, self.bubble_body)

    def show_bubble(self, pending):
        sig = json.dumps(pending, sort_keys=True)
        tool = pending.get("tool_name", "?")
        if sig != self.pending_signature:
            self.pending_signature = sig
            self.bubble_title.config(text=f"allow {tool}?")
            self.bubble_detail.config(text=self._summarize(pending))
            self._glass_refit(self.bubble_canvas, self.bubble_body)
        if not self.bubble_visible:
            # Hide the floating pill window while the permission bubble (inline
            # in the crab window) takes over.
            self._hide_pill_window()
            self.bubble_frame.pack(before=self.crab_label, pady=(0, dp(1)))
            self.bubble_visible = True
            self._reanchor()

    def hide_bubble(self):
        if self.bubble_visible:
            self.bubble_frame.pack_forget()
            self.bubble_visible = False
            self._reanchor()
            self._update_pill_window()  # bring the pills back
        self.pending_signature = None

    def _summarize(self, pending):
        ti = pending.get("tool_input") or {}
        if not isinstance(ti, dict):
            return ""
        for key in ("command", "file_path", "path", "pattern", "url"):
            v = ti.get(key)
            if v:
                v = str(v)
                if len(v) > 80:
                    v = v[:77] + "..."
                return v
        return ""

    def respond(self, decision):
        try:
            RESPONSE_FILE.write_text(
                json.dumps({"decision": decision, "ts": time.time()})
            )
        except Exception:
            pass
        self.hide_bubble()
        # If no gate consumes the response within 2s, the bubble was a fake/test
        # (no real PreToolUse hook was waiting). Clean up so it doesn't reappear.
        self.root.after(2000, self._cleanup_stale_pending)

    def _cleanup_stale_pending(self):
        if RESPONSE_FILE.exists():
            for f in (PENDING_FILE, RESPONSE_FILE):
                try:
                    f.unlink()
                except Exception:
                    pass

    # ---------- pills ----------

    # Higher number = more "active" state. Used to pick the primary session
    # when multiple sessions share the same label.
    _STATE_PRIORITY = {
        "working": 6, "permission": 5, "attention": 4, "tool": 3,
        "ready": 2, "finished": 1, "idle": 0,
    }

    def update_pills(self, sessions):
        # Undismiss any session whose file ts changed since we hid it.
        for sid in list(self.dismissed):
            data = sessions.get(sid)
            if not data:
                del self.dismissed[sid]
            elif data.get("ts", 0) > self.dismissed[sid]:
                del self.dismissed[sid]

        # Group sessions into pills. Titled sessions sharing a label collapse
        # into one pill (dedupes "Salon prep" opened 3 times). Untitled but
        # actively-working sessions each get their OWN pill keyed by sid, so
        # two new chats running at once show as two pills instead of merging
        # into a single "new chat". The "primary" session in a group is
        # whichever has the most active state. Finished/idle are hidden.
        groups = {}  # key -> {"name": display_name, "data": primary, "sids": [...]}
        for sid, data in sessions.items():
            if sid in self.dismissed:
                continue
            state = data.get("state", "idle")
            if state in ("finished", "idle"):
                continue
            # Don't show a backlog of old/done sessions on startup. A pill
            # only earns a slot if it's in an active state OR its Claude
            # process is actually still running. A stale "ready" session
            # whose process is long gone stays hidden.
            active = state in ("working", "attention", "permission", "tool")
            if not active and not data.get("live"):
                continue
            label = data.get("label")
            if label:
                key, name = label, label
            elif state == "working":
                # Untitled but active — unique key per session (so they don't
                # collapse), shown as a "new chat" placeholder until Claude
                # generates a title.
                key, name = f"\x00new\x00{sid}", "new chat"
            else:
                continue  # untitled + idle → no pill
            g = groups.get(key)
            if g is None:
                groups[key] = {"name": name, "data": data, "sids": [sid]}
            else:
                g["sids"].append(sid)
                pri_curr = self._STATE_PRIORITY.get(state, 0)
                pri_prev = self._STATE_PRIORITY.get(g["data"].get("state", "idle"), 0)
                if pri_curr > pri_prev:
                    g["data"] = data

        # Destroy pills whose group is no longer visible.
        for key in list(self.pills):
            if key not in groups:
                if self._delete_target == key:
                    self._do_hide_delete()
                self.pills[key]["frame"].destroy()
                del self.pills[key]

        for key in sorted(groups.keys()):
            g = groups[key]
            state = g["data"].get("state", "idle")
            label_text, color = STATE_LOOKS.get(state, (state, TAG_FG))
            display = f"{g['name']} · {label_text}"

            if key in self.pills:
                p = self.pills[key]
                if p["label"].cget("text") != display:
                    p["label"].config(text=display, fg=color)
                    self._refit_pill(p)
                else:
                    p["label"].config(fg=color)
                p["sids"] = g["sids"]
                p["state"] = state
                p["name"] = g["name"]
            else:
                self.pills[key] = self._make_pill(key, display, color, g["sids"])
                self.pills[key]["state"] = state
                self.pills[key]["name"] = g["name"]

    def _make_pill(self, label_key, display, color, sids):
        """Build a frosted-glass pill — a rounded, hairline-edged card drawn
        on a canvas with the status label floating on top. The delete button
        is a separate floating Toplevel shown on hover."""
        frame = tk.Frame(self.pill_frame, bg=TRANSPARENT_KEY)
        frame.pack(pady=dp(2))

        canvas = tk.Canvas(frame, bg=TRANSPARENT_KEY, highlightthickness=0, bd=0)
        canvas.pack()
        label_widget = tk.Label(
            canvas, text=display, bg=GLASS_FILL, fg=color,
            font=(GLASS_FONT, 9),
        )
        canvas._content_item = canvas.create_window(
            PILL_PAD_X, PILL_PAD_Y, anchor="nw", window=label_widget)

        for w in (frame, canvas, label_widget):
            w.bind("<ButtonPress-1>", self.on_press)
            w.bind("<B1-Motion>", self.on_motion)
            w.bind("<ButtonRelease-1>", self.on_release)
            w.bind("<Button-3>", lambda e, k=label_key: self._on_pill_right_click(e, k))
            w.bind("<Double-Button-1>", lambda e, k=label_key: self._start_rename(k))
            w.bind("<Enter>", lambda _e, k=label_key: self._show_delete_for_pill(k),
                   add="+")
            w.bind("<Leave>", lambda _e: self._hide_delete_soon(), add="+")

        pill = {"frame": frame, "canvas": canvas, "label": label_widget,
                "sids": list(sids)}
        self._refit_pill(pill)
        return pill

    def _refit_pill(self, pill, fill=GLASS_FILL):
        """Size the pill canvas to its label and (re)paint the glass card."""
        canvas, label = pill["canvas"], pill["label"]
        label.update_idletasks()
        w = label.winfo_reqwidth() + PILL_PAD_X * 2
        h = label.winfo_reqheight() + PILL_PAD_Y * 2
        canvas.configure(width=w, height=h)
        canvas.coords(canvas._content_item, PILL_PAD_X, PILL_PAD_Y)
        self._draw_glass(canvas, w, h, radius=PILL_RADIUS, fill=fill)

    def _update_pill_window(self):
        """Float the translucent pill window centered just above the crab.
        Hidden while the permission bubble shows or when there are no pills."""
        if self.bubble_visible or self._usage_visible or not self.pills:
            self._hide_pill_window()
            return
        try:
            self.root.update_idletasks()
            self.pill_frame.update_idletasks()
            pw = self.pill_frame.winfo_reqwidth()
            ph = self.pill_frame.winfo_reqheight()
            crab_x = self.root.winfo_x() + self.crab_label.winfo_x()
            crab_y = self.root.winfo_y() + self.crab_label.winfo_y()
            crab_w = self.crab_label.winfo_width()
            x = crab_x + (crab_w - pw) // 2
            # Keep long pill stacks fully on-screen when the pet is near an edge.
            x = max(dp(4), min(x, self.root.winfo_screenwidth() - pw - dp(4)))
            y = crab_y - ph - dp(4)
            self.pill_window.geometry(f"{pw}x{ph}+{x}+{y}")
            if not self._pill_window_visible:
                self.pill_window.deiconify()
                self.pill_window.attributes("-topmost", True)
                self._pill_window_visible = True
        except Exception:
            pass

    def _hide_pill_window(self):
        if self._pill_window_visible:
            try:
                self.pill_window.withdraw()
            except Exception:
                pass
            self._pill_window_visible = False

    def _on_pill_right_click(self, e, key):
        """Pill-specific context menu: rename or dismiss this session."""
        pill = self.pills.get(key)
        sids = pill.get("sids", []) if pill else []
        items = [
            ("✏️  Rename", lambda: self._start_rename(key)),
            ("✕  Dismiss", lambda: self.dismiss_label(key, sids)),
        ]
        self._show_menu(items, e.x_root, e.y_root)

    def _start_rename(self, key):
        """Pop a small inline entry above the pill to rename the session.
        Enter saves (persists + overrides the transcript title), Esc cancels."""
        pill = self.pills.get(key)
        if not pill:
            return
        # Close the delete button + any open menu so they don't overlap.
        self._do_hide_delete()
        if self._rename_window is not None:
            try:
                self._rename_window.destroy()
            except Exception:
                pass
            self._rename_window = None

        current = pill.get("name", "")
        if current == "new chat":
            current = ""

        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg="#3a3a42")
        inner = tk.Frame(win, bg="#1f1f24")
        inner.pack(padx=1, pady=1)
        entry = tk.Entry(
            inner, width=24, bg="#1f1f24", fg="#e8e8ec",
            insertbackground="#e8e8ec", relief="flat",
            font=("Segoe UI", 10), bd=0,
        )
        entry.pack(padx=dp(8), pady=dp(6), ipady=dp(2))
        entry.insert(0, current)
        entry.select_range(0, "end")

        def commit(_e=None):
            new_name = entry.get().strip()
            self._finish_rename(win, key, new_name)

        def cancel(_e=None):
            self._finish_rename(win, key, None)

        entry.bind("<Return>", commit)
        entry.bind("<Escape>", cancel)
        entry.bind("<FocusOut>", cancel)

        # Position just above the pill.
        try:
            self.root.update_idletasks()
            frame = pill["frame"]
            px = frame.winfo_rootx()
            py = frame.winfo_rooty() - frame.winfo_height() - dp(6)
            win.geometry(f"+{px}+{py}")
        except Exception:
            pass

        self._rename_window = win
        win.focus_force()
        entry.focus_set()

    def _finish_rename(self, win, key, new_name):
        if self._rename_window is not win:
            return  # already closed
        self._rename_window = None
        try:
            win.destroy()
        except Exception:
            pass
        if new_name is None:
            return  # cancelled
        pill = self.pills.get(key)
        sids = pill.get("sids", []) if pill else []
        for sid in sids:
            if new_name:
                self.session_names[sid] = new_name
            else:
                self.session_names.pop(sid, None)  # empty clears the override
        self._save_session_names()
        # Reflect immediately without waiting for the next poll.
        self.sessions = self.load_sessions()
        self.update_pills(self.sessions)
        self._reanchor()
        self._update_pill_window()

    def dismiss_label(self, label_key, sids):
        """Dismiss every session_id that this label-pill represents."""
        for sid in sids:
            data = self.sessions.get(sid)
            if data:
                self.dismissed[sid] = data.get("ts", 0)
        if self._delete_target == label_key:
            self._do_hide_delete()
        if label_key in self.pills:
            self.pills[label_key]["frame"].destroy()
            del self.pills[label_key]
        self._reanchor()

    # ---------- positioning / drag ----------

    def load_position(self):
        self.root.update_idletasks()
        h = self.root.winfo_reqheight()
        w = self.root.winfo_reqwidth()
        try:
            pos = json.loads(POSITION_FILE.read_text())
            # Positions saved before sharp mode are in 96-DPI coordinates.
            k = UI_SCALE / float(pos.get("scale", 1.0))
            pos = {key: (v * k if isinstance(v, (int, float)) and key != "scale" else v)
                   for key, v in pos.items()}
            if "center_x" in pos:
                self._anchor_center_x = int(pos["center_x"])
                self._anchor_bottom = int(pos["bottom"])
            elif "bottom" in pos:
                # Migrate from top-left + bottom format.
                self._anchor_center_x = int(pos["x"]) + w // 2
                self._anchor_bottom = int(pos["bottom"])
            else:
                # Migrate from oldest top-left + top format.
                self._anchor_center_x = int(pos["x"]) + w // 2
                self._anchor_bottom = int(pos["y"]) + h
            self.root.geometry(f"+{self._anchor_center_x - w // 2}+{self._anchor_bottom - h}")
            return
        except Exception:
            pass
        # Default position — bottom-right of the WORK area (above taskbar).
        wa = get_work_area()
        if wa:
            _, _, wa_right, wa_bottom = wa
            self._anchor_center_x = wa_right - w // 2 - dp(30)
            self._anchor_bottom = wa_bottom - dp(20)
        else:
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            self._anchor_center_x = sw - w // 2 - dp(30)
            self._anchor_bottom = sh - dp(80)
        # Offset duplicates so they don't spawn exactly on top of the original.
        if INSTANCE_ID != "default":
            off = dp((abs(hash(INSTANCE_ID)) % 120) + 30)
            self._anchor_center_x -= off
            self._anchor_bottom -= off // 2
        self.root.geometry(f"+{self._anchor_center_x - w // 2}+{self._anchor_bottom - h}")

    def save_position(self):
        try:
            x = self.root.winfo_x()
            y = self.root.winfo_y()
            w = self.root.winfo_width()
            h = self.root.winfo_height()
            self._anchor_center_x = x + w // 2
            self._anchor_bottom = y + h
            POSITION_FILE.write_text(
                json.dumps({"center_x": self._anchor_center_x,
                            "bottom": self._anchor_bottom,
                            "scale": UI_SCALE})
            )
        except Exception:
            pass

    def _reanchor(self):
        """Pin the crab in place: the bottom edge AND the horizontal center stay
        fixed, so widening/narrowing the pet (pill X'd out, bubble appearing,
        rename growing the pill) doesn't shift the crab left or right or up."""
        if self._dragged:
            return
        if not hasattr(self, "_anchor_bottom"):
            return
        self.root.update_idletasks()
        w = self.root.winfo_width()
        h = self.root.winfo_height()
        target_x = self._anchor_center_x - w // 2
        target_y = self._anchor_bottom - h
        # Apply Y-clamp if enabled (matches the drag behavior).
        if self.settings.get("clamp_y"):
            wa = get_work_area()
            if wa:
                _, _, _, wa_bottom = wa
                if target_y + h > wa_bottom:
                    target_y = wa_bottom - h
                    self._anchor_bottom = target_y + h
        if self.root.winfo_x() != target_x or self.root.winfo_y() != target_y:
            self.root.geometry(f"+{target_x}+{target_y}")

    def on_press(self, e):
        self._press_x = e.x_root
        self._press_y = e.y_root
        self._win_x = self.root.winfo_x()
        self._win_y = self.root.winfo_y()
        self._dragged = False
        # Hide the side emote during interaction — it'll re-appear on the next
        # mood check if a session is still working.
        if self._emote_visible:
            self.hide_emote()
        if self._delete_visible:
            self._do_hide_delete()

    def on_motion(self, e):
        dx = e.x_root - self._press_x
        dy = e.y_root - self._press_y
        if not self._dragged and (abs(dx) > dp(4) or abs(dy) > dp(4)):
            self._dragged = True
            self.update_animation()
        if self._dragged:
            new_x = self._win_x + dx
            new_y = self._win_y + dy
            # Optional Y-clamp (settings["clamp_y"]) to keep the crab above the
            # Windows taskbar. Off by default — taskbar will then cover the
            # crab on click, but Tian can sit him anywhere.
            if self.settings.get("clamp_y"):
                wa = get_work_area()
                if wa:
                    _, _, _, wa_bottom = wa
                    h = self.root.winfo_height()
                    if new_y + h > wa_bottom:
                        new_y = wa_bottom - h
            self.root.geometry(f"+{new_x}+{new_y}")
            # Shake-to-clear: detect rapid horizontal direction reversals.
            now = time.time()
            self._shake_pts.append((now, e.x_root))
            self._shake_pts = [(t, x) for (t, x) in self._shake_pts if now - t < 0.8]
            if (now >= self._shake_cooldown_until
                    and len(self._shake_pts) >= 6):
                reversals = 0
                prev_sign = 0
                for i in range(1, len(self._shake_pts)):
                    seg = self._shake_pts[i][1] - self._shake_pts[i - 1][1]
                    if abs(seg) < dp(8):
                        continue
                    sign = 1 if seg > 0 else -1
                    if prev_sign != 0 and sign != prev_sign:
                        reversals += 1
                    prev_sign = sign
                if reversals >= 3:
                    self._shake_clear_idle()
                    self._shake_pts.clear()
                    self._shake_cooldown_until = now + 1.5

    def on_release(self, e):
        was_dragged = self._dragged
        self._dragged = False
        if was_dragged:
            self.save_position()
            self.update_animation()
        elif e.widget is self.crab_label:
            self.toggle_usage_panel()
        else:
            self.focus_or_launch_claude()

    # ---------- right-click menu ----------

    def on_right_click(self, e):
        chime_item = ("🔇 Mute chime", self.toggle_chime) \
            if self.settings.get("chime") else ("🔔 Enable chime", self.toggle_chime)
        if autostart_installed():
            autostart_item = ("Disable autostart", self._toggle_autostart)
        else:
            autostart_item = ("Enable autostart", self._toggle_autostart)

        clamp_item = ("Allow taskbar overlap", self.toggle_clamp_y) \
            if self.settings.get("clamp_y") else ("Lock above taskbar", self.toggle_clamp_y)
        base_name = "dragon" if THEME == "dragon" else "crab"
        if self.character == "crow":
            char_item = (f"{'🐉' if THEME == 'dragon' else '🦀'} Switch to {base_name}",
                         lambda: self._switch_character(THEME))
        else:
            char_item = ("🐦 Switch to crow", lambda: self._switch_character("crow"))
        items = [
            ("Open Claude", self.focus_or_launch_claude),
            ("📊 Usage", self.toggle_usage_panel),
            ("🎉 Cheer", self._manual_cheer),
            char_item,
            (f"✨ Duplicate {self.character}", self._duplicate_self),
            chime_item,
            clamp_item,
            "---",
            ("Clear sessions", self.clear_sessions),
            ("Clear always-allow", self.clear_always_allow),
            autostart_item,
            "---",
            ("Quit crab", self.quit_pet),
        ]
        self._show_menu(items, e.x_root, e.y_root)

    def _show_menu(self, items, x, y):
        """Custom modern context menu — replaces blurry Win32 tk.Menu rendering
        with a Tk Toplevel using a clean Segoe UI font and blue hover accent."""
        existing = getattr(self, "_active_menu", None)
        if existing is not None:
            try:
                existing.destroy()
            except Exception:
                pass
            self._active_menu = None

        BG = "#1f1f24"
        BORDER = "#3a3a42"
        FG = "#e8e8ec"
        HOVER_BG = "#3766c6"

        menu = tk.Toplevel(self.root)
        menu.overrideredirect(True)
        menu.attributes("-topmost", True)
        menu.configure(bg=BORDER)
        inner = tk.Frame(menu, bg=BG)
        inner.pack(padx=1, pady=1)

        for entry in items:
            if entry == "---":
                tk.Frame(inner, bg=BORDER, height=1).pack(fill="x", padx=dp(2), pady=dp(4))
                continue
            label_text, command = entry
            lbl = tk.Label(
                inner, text=label_text,
                bg=BG, fg=FG,
                font=("Segoe UI", 10),
                padx=dp(22), pady=dp(7),
                anchor="w",
            )
            lbl.pack(fill="x")
            self._wire_menu_item(menu, lbl, command, BG, HOVER_BG)

        # Position; nudge inward if going off-screen.
        menu.update_idletasks()
        mw, mh = menu.winfo_width(), menu.winfo_height()
        sw, sh = menu.winfo_screenwidth(), menu.winfo_screenheight()
        if x + mw > sw - 4:
            x = max(0, sw - mw - 4)
        if y + mh > sh - 4:
            y = max(0, sh - mh - 4)
        menu.geometry(f"+{x}+{y}")

        menu.focus_force()
        menu.bind("<FocusOut>", lambda _e: self._close_menu(menu))
        menu.bind("<Escape>", lambda _e: self._close_menu(menu))
        self._active_menu = menu

    def _wire_menu_item(self, menu, lbl, command, normal_bg, hover_bg):
        def on_enter(_e):
            lbl.config(bg=hover_bg)

        def on_leave(_e):
            lbl.config(bg=normal_bg)

        def on_click(_e):
            self._close_menu(menu)
            if command:
                self.root.after_idle(command)

        lbl.bind("<Enter>", on_enter)
        lbl.bind("<Leave>", on_leave)
        lbl.bind("<Button-1>", on_click)

    def _close_menu(self, menu):
        try:
            menu.destroy()
        except Exception:
            pass
        if getattr(self, "_active_menu", None) is menu:
            self._active_menu = None

    def _manual_cheer(self):
        """Trigger a 2-second cheer animation on demand."""
        self._cheer_until = time.time() + 2.0
        self.update_animation()

    # ---------- duplicate / quit ----------

    def _duplicate_self(self):
        """Spawn a new pet process with its own instance ID + position file.
        Inherits the current THEME so duplicates of the dragon are dragons."""
        import uuid
        new_id = "dup_" + uuid.uuid4().hex[:6]
        exe = sys.executable
        pw = Path(exe).parent / "pythonw.exe"
        if pw.exists():
            exe = str(pw)
        args = [exe, str(Path(__file__).resolve())]
        if THEME != "crab":
            args.append(f"--{THEME}")
        if self.character == "crow":
            args.append("--crow")
        args.append(new_id)
        label = self.character
        try:
            subprocess.Popen(args, creationflags=_NO_WINDOW)
            self.show_toast(f"✨ Spawned a new {label}")
        except Exception:
            self.show_toast("✗ Couldn't duplicate")

    def quit_pet(self):
        """Quit this pet instance. Duplicates clean up their position file
        so they don't leave orphaned state behind."""
        if INSTANCE_ID != "default":
            try:
                POSITION_FILE.unlink()
            except Exception:
                pass
        self.root.destroy()

    # ---------- toast notifications ----------

    def show_toast(self, text, duration_ms=2200):
        """Pop a small dark notification above the crab for a couple of seconds."""
        existing = getattr(self, "_toast_window", None)
        if existing is not None:
            try:
                existing.destroy()
            except Exception:
                pass
            self._toast_window = None

        toast = tk.Toplevel(self.root)
        toast.overrideredirect(True)
        toast.attributes("-topmost", True)
        try:
            toast.attributes("-transparentcolor", TRANSPARENT_KEY)
            toast.attributes("-alpha", GLASS_ALPHA)
        except tk.TclError:
            pass
        toast.configure(bg=TRANSPARENT_KEY)

        canvas, inner = self._glass_wrap(toast)
        canvas.pack()
        tk.Label(
            inner, text=text,
            bg=GLASS_FILL, fg=GLASS_FG, font=("Segoe UI", 9),
        ).pack()
        self._glass_refit(canvas, inner)

        # Center horizontally on the crab, sit just above it.
        self.root.update_idletasks()
        crab_x = self.root.winfo_x() + self.crab_label.winfo_x()
        crab_y = self.root.winfo_y() + self.crab_label.winfo_y()
        crab_w = self.crab_label.winfo_width()
        toast.update_idletasks()
        tw = toast.winfo_reqwidth()
        th = toast.winfo_reqheight()
        toast.geometry(f"+{crab_x + (crab_w - tw) // 2}+{crab_y - th - dp(4)}")

        self._toast_window = toast
        self.root.after(duration_ms, lambda: self._destroy_toast(toast))

    def _destroy_toast(self, toast):
        try:
            toast.destroy()
        except Exception:
            pass
        if getattr(self, "_toast_window", None) is toast:
            self._toast_window = None

    # ---------- autostart toggle with feedback ----------

    def _toggle_autostart(self):
        """Toggle autostart with a working-indicator toast and a result toast."""
        if autostart_installed():
            self.show_toast("Disabling autostart…", duration_ms=8000)
            target = uninstall_autostart
            on_msg = "✓ Autostart disabled"
            off_msg = "✗ Couldn't disable"
        else:
            self.show_toast("Enabling autostart…", duration_ms=8000)
            target = install_autostart
            on_msg = "✓ Autostart enabled"
            off_msg = "✗ Couldn't enable"

        def worker():
            ok = target()
            self.root.after_idle(
                lambda: self.show_toast(on_msg if ok else off_msg)
            )

        threading.Thread(target=worker, daemon=True).start()

    def focus_or_launch_claude(self):
        """Bring Claude Desktop to front, or launch it if not running."""
        if not self._claude_launcher_discovered:
            self._claude_launcher_cache = find_claude_desktop_launcher()
            self._claude_launcher_discovered = True
        focus_or_launch_claude(self._claude_launcher_cache)

    # ---------- floating delete button ----------

    def _build_delete_button(self):
        self.delete_window = tk.Toplevel(self.root)
        self.delete_window.overrideredirect(True)
        self.delete_window.attributes("-topmost", True)
        try:
            self.delete_window.attributes("-transparentcolor", TRANSPARENT_KEY)
        except tk.TclError:
            pass
        self.delete_window.configure(bg=TRANSPARENT_KEY)

        inner = tk.Frame(self.delete_window, bg=TAG_BG)
        inner.pack()
        # Match the pill's font + padding exactly so the bubble height lines up.
        self.delete_label = tk.Label(
            inner, text="−",
            bg=TAG_BG, fg="#cccccc",
            font=("Segoe UI", 9, "bold"),
            padx=dp(12), pady=dp(3), cursor="hand2",
        )
        self.delete_label.pack()

        # Hovering the delete button itself keeps it shown; clicking dismisses.
        self.delete_label.bind(
            "<Enter>",
            lambda _e: (self._cancel_delete_hide(),
                        self.delete_label.config(fg="#ff6666")),
        )
        self.delete_label.bind(
            "<Leave>",
            lambda _e: (self._hide_delete_soon(),
                        self.delete_label.config(fg="#cccccc")),
        )
        self.delete_label.bind("<Button-1>", self._on_delete_click)
        self.delete_window.withdraw()

    def _show_delete_for_pill(self, label_key):
        pill = self.pills.get(label_key)
        if not pill:
            return
        self._cancel_delete_hide()
        frame = pill["frame"]
        try:
            self.root.update_idletasks()
            px = frame.winfo_rootx() + frame.winfo_width() + dp(6)
            py = frame.winfo_rooty()
            self.delete_window.geometry(f"+{px}+{py}")
            if not self._delete_visible:
                self.delete_window.deiconify()
                self.delete_window.attributes("-topmost", True)
                self._delete_visible = True
            self._delete_target = label_key
        except tk.TclError:
            pass

    def _cancel_delete_hide(self):
        if self._delete_hide_after is not None:
            try:
                self.root.after_cancel(self._delete_hide_after)
            except Exception:
                pass
            self._delete_hide_after = None

    def _hide_delete_soon(self):
        # Schedule a hide after a short grace period so the user can move from
        # the pill to the delete button without it disappearing in between.
        if self._delete_hide_after is not None:
            return
        self._delete_hide_after = self.root.after(120, self._do_hide_delete)

    def _do_hide_delete(self):
        self._delete_hide_after = None
        if self._delete_visible:
            try:
                self.delete_window.withdraw()
            except tk.TclError:
                pass
            self._delete_visible = False
        self._delete_target = None

    def _on_delete_click(self, _event=None):
        target = self._delete_target
        self._do_hide_delete()
        if target and target in self.pills:
            sids = self.pills[target].get("sids", [])
            self.dismiss_label(target, sids)

    def _load_png(self, name):
        """Load a single PNG sprite from the pet folder."""
        path = ROOT / f"{name}.png"
        if not path.exists():
            return None
        try:
            img = Image.open(path).convert("RGBA")
            return ImageTk.PhotoImage(img)
        except Exception:
            return None

    def _load_animated(self, base_name):
        """Load animation frames for a sprite state. Tries, in order:
           1. <base_name>.gif         — extract every frame
           2. <base_name>_<N>.png     — numbered PNGs starting at 0 (or 1)
           3. <base_name>.png         — single frame
        Returns a list of ImageTk.PhotoImage frames, or None if nothing found.
        """
        # 1) Animated GIF
        gif_path = ROOT / f"{base_name}.gif"
        if gif_path.exists():
            try:
                gif = Image.open(gif_path)
                frames = []
                try:
                    while True:
                        frame = gif.copy().convert("RGBA")
                        frames.append(ImageTk.PhotoImage(frame))
                        gif.seek(gif.tell() + 1)
                except EOFError:
                    pass
                if frames:
                    return frames
            except Exception:
                pass

        # 2) Numbered PNGs — accept 0-indexed or 1-indexed
        for start in (0, 1):
            frames = []
            i = start
            while True:
                p = ROOT / f"{base_name}_{i}.png"
                if not p.exists():
                    break
                try:
                    frames.append(ImageTk.PhotoImage(
                        Image.open(p).convert("RGBA")))
                    i += 1
                except Exception:
                    break
            if frames:
                return frames

        # 3) Single PNG fallback
        photo = self._load_png(base_name)
        if photo:
            return [photo]
        return None

    def _apply_png_overrides(self):
        """Replace the hand-coded sprites with custom art from the pet folder
        if present. Supports GIFs (auto-extracted), numbered PNG sequences,
        or single PNGs. Any state without a custom file uses the built-in."""
        prefix = THEME

        standing = self._load_animated(f"{prefix}_standing")
        if standing:
            self.standing_tk = standing[0]  # standing is always a single image

        for state, attr in (
            ("dangle",   "dangle_tks"),
            ("typing",   "typing_tks"),
            ("cheer",    "cheer_tks"),
            ("sleeping", "sleeping_tks"),
        ):
            frames = self._load_animated(f"{prefix}_{state}")
            if frames:
                setattr(self, attr, frames)

    # ---------- characters (crab / dragon / crow) ----------

    CRAB_INTERVALS = {"standing": 0, "dangle": 150, "cheer": 200, "hop": 100,
                      "typing": 220, "sleeping": 650}

    def _load_character(self, name, crow_frames=None):
        """Build the PhotoImage frame lists for the pixel crab/dragon or the
        vector crow. crow_frames = PIL frames already rendered off-thread."""
        if name == "crow":
            frames = crow_frames or crow_art.load_or_build(STATE_DIR / "crow_frames", scale=UI_SCALE)

            def photos(key):
                return [ImageTk.PhotoImage(im) for im in frames[key]]

            self.standing_tks = photos("idle")
            self.standing_tk = self.standing_tks[0]
            self.typing_tks = photos("typing")
            self.sleeping_tks = photos("sleeping")
            self.cheer_tks = photos("cheer")
            self.dangle_tks = photos("dangle")
            self.hop_tks = photos("hop")
            self.anim_intervals = dict(crow_art.INTERVALS)
        else:
            self.standing_tk = ImageTk.PhotoImage(crab_image(STANDING))
            self.standing_tks = None      # crab stance is a single static frame
            self.dangle_tks = [ImageTk.PhotoImage(crab_image(f)) for f in DANGLE_FRAMES]
            self.typing_tks = [ImageTk.PhotoImage(crab_image(f)) for f in TYPING_FRAMES]
            self.cheer_tks = [ImageTk.PhotoImage(crab_image(f)) for f in CHEER_FRAMES]
            self.hop_tks = [ImageTk.PhotoImage(crab_image(f)) for f in HOP_FRAMES]
            self.sleeping_tks = [
                ImageTk.PhotoImage(crab_image(SLEEPING_BODY, zs=zs))
                for zs in SLEEPING_Z_FRAMES
            ]
            self.anim_intervals = dict(self.CRAB_INTERVALS)
            # Custom PNG/GIF overrides: <theme>_<state>.png next to pet.py.
            self._apply_png_overrides()
        self.character = name

    def _switch_character(self, name):
        if name == self.character:
            return
        if name == "crow" and crow_art is None:
            self.show_toast("✗ crow.py is missing")
            return
        self.settings[self._char_key] = name
        save_settings(self.settings)
        if name != "crow":
            self._apply_character(name, None)
            return
        # Rendering the crow takes ~1s the first time; do it off the UI thread.
        self.show_toast("🥚 Hatching crow…", duration_ms=8000)

        def worker():
            try:
                frames = crow_art.load_or_build(STATE_DIR / "crow_frames", scale=UI_SCALE)
            except Exception:
                frames = None
            self.root.after(0, lambda: self._apply_character("crow", frames))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_character(self, name, crow_frames):
        if name == "crow" and crow_frames is None:
            self.show_toast("✗ Couldn't hatch the crow")
            return
        self._load_character(name, crow_frames)
        self._current_anim = None            # force the new frames to play
        self.crab_label.config(image=self.standing_tk)
        self.update_animation()
        self._reanchor()
        self._update_pill_window()
        self.show_toast("🐦 Caw!" if name == "crow" else f"Back to {name}")

    # ---------- usage panel (click the pet) ----------

    USAGE_W, USAGE_H = dp(248), dp(194)
    USAGE_ORANGE = "#d97757"
    USAGE_TRACK = "#474b58"

    def _usage_refresh_worker(self, then_render=False):
        """Background: update the transcript index and recompute stats."""
        with self._usage_lock:
            try:
                if self._usage_index is None:
                    self._usage_index = usage_stats.UsageIndex(STATE_DIR / "usage_index.json")
                self._usage_index.refresh()
                stats = self._usage_index.stats()
                stats["at"] = time.time()
            except Exception:
                stats = None
        if stats is not None:
            self._usage_stats = stats
        if then_render:
            self.root.after(0, self._render_usage_panel)

    def toggle_usage_panel(self):
        if self._usage_visible:
            self._close_usage_panel()
            return
        if time.time() - self._usage_closed_at < 0.35:
            return   # this click is the one whose focus change just closed it
        if usage_stats is None:
            self.show_toast("✗ usage.py is missing")
            return
        if self._usage_window is None:
            self._build_usage_window()
        self._usage_visible = True
        self._usage_opened_at = time.time()
        self._hide_pill_window()
        self._render_usage_panel()
        self._usage_window.deiconify()
        self._usage_window.attributes("-topmost", True)
        self._update_usage_position()
        try:
            self._usage_window.focus_force()
        except Exception:
            pass
        self._usage_tick()

    def _usage_tick(self):
        """While open: refresh data + countdown every 20s."""
        if not self._usage_visible:
            return
        threading.Thread(target=self._usage_refresh_worker,
                         kwargs={"then_render": True}, daemon=True).start()
        self._usage_tick_after = self.root.after(20000, self._usage_tick)

    def _close_usage_panel(self, event=None):
        if not self._usage_visible:
            return
        is_focus_out = event is not None and str(getattr(event, "type", "")) == "FocusOut"
        if is_focus_out and time.time() - self._usage_opened_at < 0.3:
            return   # focus settling right after opening, not a real click-away
        self._usage_visible = False
        self._usage_closed_at = time.time()
        if self._usage_tick_after is not None:
            try:
                self.root.after_cancel(self._usage_tick_after)
            except Exception:
                pass
            self._usage_tick_after = None
        try:
            self._usage_window.withdraw()
        except Exception:
            pass
        self._update_pill_window()

    def _build_usage_window(self):
        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        try:
            win.attributes("-transparentcolor", TRANSPARENT_KEY)
            win.attributes("-alpha", GLASS_ALPHA)
        except tk.TclError:
            pass
        win.configure(bg=TRANSPARENT_KEY)
        canvas = tk.Canvas(win, width=self.USAGE_W, height=self.USAGE_H,
                           bg=TRANSPARENT_KEY, highlightthickness=0, bd=0)
        canvas.pack()
        # Dismiss: click the panel, click anywhere else, or Esc.
        canvas.bind("<Button-1>", self._close_usage_panel)
        win.bind("<Escape>", self._close_usage_panel)
        win.bind("<FocusOut>", self._close_usage_panel)
        win.withdraw()
        self._usage_window, self._usage_canvas = win, canvas

    def _usage_bar(self, c, x0, y0, x1, h, frac, fill):
        """Rounded track + rounded fill."""
        r = h / 2
        c.create_polygon(_round_rect_points(x0, y0, x1, y0 + h, r), smooth=True,
                         fill=self.USAGE_TRACK, outline="")
        frac = max(0.0, min(1.0, frac))
        if frac <= 0:
            return
        xf = x0 + max(h, (x1 - x0) * frac)
        c.create_polygon(_round_rect_points(x0, y0, xf, y0 + h, r), smooth=True,
                         fill=fill, outline="")

    def _render_usage_panel(self):
        if not self._usage_visible or self._usage_canvas is None:
            return
        c, wd, ht = self._usage_canvas, self.USAGE_W, self.USAGE_H
        c.delete("all")
        self._draw_glass(c, wd, ht)
        pad = dp(16)
        f_title = (GLASS_FONT, 10)
        f_label = (GLASS_FONT, 8)
        f_value = (GLASS_FONT, 9)
        c.create_text(pad, dp(22), text="Claude usage", anchor="w", fill=GLASS_FG, font=f_title)
        st = self._usage_stats
        if st is None:
            c.create_text(wd / 2, ht / 2 + dp(8), text="Reading transcripts…",
                          fill=GLASS_FG_DIM, font=f_label)
            return
        fmt = usage_stats.fmt_tokens

        # Session: progress through Claude's 5-hour window + reset countdown.
        y = dp(48)
        c.create_text(pad, y, text="Session", anchor="w", fill=GLASS_FG_DIM, font=f_label)
        left = None
        if st["session_resets_in"] is not None:
            left = st["session_resets_in"] - (time.time() - st["at"])
        if left is not None and left > 0:
            c.create_text(wd - pad, y, text=f"resets in {usage_stats.fmt_duration(left)}",
                          anchor="e", fill=GLASS_FG_DIM, font=f_label)
            elapsed = 1 - left / usage_stats.BLOCK
            self._usage_bar(c, pad, y + dp(12), wd - pad, dp(8), elapsed, self.USAGE_ORANGE)
            c.create_text(pad, y + dp(34), text=f"{fmt(st['session'])} tokens",
                          anchor="w", fill=GLASS_FG, font=f_value)
        else:
            c.create_text(wd - pad, y, text="idle", anchor="e",
                          fill=GLASS_FG_DIM, font=f_label)
            self._usage_bar(c, pad, y + dp(12), wd - pad, dp(8), 0, self.USAGE_ORANGE)
            c.create_text(pad, y + dp(34), text="No active session", anchor="w",
                          fill=GLASS_FG, font=f_value)
        c.create_text(wd - pad, y + dp(34), text=f"{st['replies_today']} replies today",
                      anchor="e", fill=GLASS_FG_DIM, font=f_label)

        # Week: daily bars for the last 7 days (today in orange).
        y = dp(110)
        c.create_text(pad, y, text="This week", anchor="w", fill=GLASS_FG_DIM, font=f_label)
        c.create_text(wd - pad, y, text=fmt(st["week"]), anchor="e",
                      fill=GLASS_FG, font=f_value)
        days = st["days"]
        top, base = y + dp(14), y + dp(56)
        peak = max((t for _, t in days), default=0) or 1
        slot = (wd - pad * 2) / len(days)
        bw = dp(16)
        for i, (letter, tok) in enumerate(days):
            cx = pad + slot * i + slot / 2
            today = i == len(days) - 1
            hgt = max(dp(3), (base - top) * tok / peak) if tok else dp(3)
            color = self.USAGE_ORANGE if today else ("#8a93ad" if tok else self.USAGE_TRACK)
            c.create_polygon(_round_rect_points(cx - bw / 2, base - hgt, cx + bw / 2, base,
                                                min(dp(4), hgt / 2)),
                             smooth=True, fill=color, outline="")
            c.create_text(cx, base + dp(11), text=letter,
                          fill=GLASS_FG if today else GLASS_FG_DIM, font=f_label)

    def _update_usage_position(self):
        """Centered above the pet (or below if there's no room), on-screen."""
        if not self._usage_visible or self._usage_window is None:
            return
        try:
            self.root.update_idletasks()
            crab_x = self.root.winfo_x() + self.crab_label.winfo_x()
            crab_w = self.crab_label.winfo_width()
            top = self.root.winfo_y()
            bottom = top + self.root.winfo_height()
            sw = self.root.winfo_screenwidth()
            x = crab_x + (crab_w - self.USAGE_W) // 2
            x = max(dp(4), min(x, sw - self.USAGE_W - dp(4)))
            y = top - self.USAGE_H - dp(6)
            if y < dp(4):
                y = bottom + dp(6)
            self._usage_window.geometry(f"+{x}+{y}")
        except Exception:
            pass

    # ---------- MCP reauth bubble ----------

    def _snapshot_mcp_cache(self):
        """Record current timestamps so we don't pop bubbles for entries that
        were already in the cache when the pet started."""
        seen = {}
        try:
            if self._mcp_auth_file.exists():
                data = json.loads(self._mcp_auth_file.read_text())
                if isinstance(data, dict):
                    for name, info in data.items():
                        if isinstance(info, dict):
                            seen[name] = info.get("timestamp", 0)
        except Exception:
            pass
        return seen

    def _build_mcp_bubble(self):
        """A persistent floating glass bubble: 'X needs reauth' + Reauth /
        Dismiss."""
        self.mcp_window = tk.Toplevel(self.root)
        self.mcp_window.overrideredirect(True)
        self.mcp_window.attributes("-topmost", True)
        try:
            self.mcp_window.attributes("-transparentcolor", TRANSPARENT_KEY)
            self.mcp_window.attributes("-alpha", GLASS_ALPHA)
        except tk.TclError:
            pass
        self.mcp_window.configure(bg=TRANSPARENT_KEY)

        self.mcp_canvas, inner = self._glass_wrap(self.mcp_window)
        self.mcp_canvas.pack()

        tk.Label(
            inner, text="🔑", bg=GLASS_FILL, fg=GLASS_FG,
            font=("Segoe UI Emoji", 15),
        ).pack()

        self.mcp_title_label = tk.Label(
            inner, text="MCP needs reauth",
            bg=GLASS_FILL, fg=GLASS_FG, font=("Segoe UI", 10, "bold"),
        )
        self.mcp_title_label.pack(pady=(dp(3), dp(10)))

        btn_row = tk.Frame(inner, bg=GLASS_FILL)
        btn_row.pack()
        self._glass_button(btn_row, "Reauth", "#30d158", "#43dd68",
                           self._mcp_reauth_clicked).pack(side="left", padx=(0, dp(6)))
        self._glass_button(btn_row, "Dismiss", "#48484a", "#5a5a5c",
                           self._mcp_dismiss_clicked).pack(side="left")

        self._glass_refit(self.mcp_canvas, inner)
        self.mcp_window.withdraw()

    def _check_mcp_reauth(self):
        """Look for new or updated entries in the MCP auth cache and pop the
        bubble for the first one we haven't shown yet."""
        if self._mcp_visible:
            return
        try:
            if not self._mcp_auth_file.exists():
                return
            data = json.loads(self._mcp_auth_file.read_text())
            if not isinstance(data, dict):
                return
        except Exception:
            return
        for name, info in data.items():
            if not isinstance(info, dict):
                continue
            ts = info.get("timestamp", 0)
            if ts > self._mcp_seen_ts.get(name, 0):
                self._mcp_seen_ts[name] = ts
                self._show_mcp_bubble(name)
                return

    def _show_mcp_bubble(self, server_name):
        # Trim the "claude.ai " prefix so the title is just the server name.
        display = server_name
        if display.startswith("claude.ai "):
            display = display[len("claude.ai "):]
        self._mcp_current_server = server_name
        self.mcp_title_label.config(text=f"{display} needs reauth")
        self._glass_refit(self.mcp_canvas, self.mcp_title_label.master)
        self._update_mcp_position()
        self.mcp_window.deiconify()
        self.mcp_window.attributes("-topmost", True)
        self._mcp_visible = True
        if self.settings.get("chime"):
            play_chime("attention")

    def _update_mcp_position(self):
        """Pin the MCP bubble flush to the LEFT of the crab, vertically
        centered. Called both on show and on every topmost tick so it follows
        the crab as you drag."""
        try:
            self.root.update_idletasks()
            crab_x = self.root.winfo_x() + self.crab_label.winfo_x()
            crab_y = self.root.winfo_y() + self.crab_label.winfo_y()
            crab_h = self.crab_label.winfo_height()
            self.mcp_window.update_idletasks()
            mw = self.mcp_window.winfo_reqwidth()
            mh = self.mcp_window.winfo_reqheight()
            # Overlap the crab's left edge by 14px so the bubble visually
            # "connects" to the body (mirrors the emote on the right).
            ex = crab_x - mw + dp(14)
            ey = crab_y + (crab_h - mh) // 2
            self.mcp_window.geometry(f"+{ex}+{ey}")
        except Exception:
            pass

    def _hide_mcp_bubble(self):
        if self._mcp_visible:
            try:
                self.mcp_window.withdraw()
            except Exception:
                pass
            self._mcp_visible = False
        self._mcp_current_server = None

    def _mcp_reauth_clicked(self):
        self._hide_mcp_bubble()
        self.focus_or_launch_claude()
        self.show_toast("Run /mcp in Claude to reauth")

    def _mcp_dismiss_clicked(self):
        # Seen-ts is already updated so the same timestamp won't re-pop.
        self._hide_mcp_bubble()

    def _build_emote_window(self):
        """Create the floating side-bubble window for the mood emoji."""
        self.emote_window = tk.Toplevel(self.root)
        self.emote_window.overrideredirect(True)
        self.emote_window.attributes("-topmost", True)
        try:
            self.emote_window.attributes("-transparentcolor", TRANSPARENT_KEY)
        except tk.TclError:
            pass
        self.emote_window.configure(bg=TRANSPARENT_KEY)
        # Transparent emoji — floats next to the crab without a dark chip.
        self.emote_label = tk.Label(
            self.emote_window,
            text="",
            bg=TRANSPARENT_KEY, fg=TAG_FG,
            font=("Segoe UI Emoji", 14),
            bd=0,
        )
        self.emote_label.pack()
        self.emote_window.withdraw()

    def _get_emoji_photo(self, emoji):
        """Cache + return a PhotoImage of the color-rendered emoji."""
        if emoji not in self._emoji_cache:
            img = render_emoji(emoji)
            if img is not None:
                self._emoji_cache[emoji] = ImageTk.PhotoImage(img)
            else:
                self._emoji_cache[emoji] = None
        return self._emoji_cache[emoji]

    def _update_emote(self, now):
        """Show the mood bubble next to the crab ONLY when a labeled session
        is actually working AND its transcript expresses a detectable mood.
        No random/fake emotes — emote follows Claude's actual sentiment."""
        # Only emote for sessions that are shown as a working pill — keeps
        # the emoji bubbles in lock-step with what Tian actually sees.
        working_sids = set()
        for pill in self.pills.values():
            if pill.get("state") == "working":
                working_sids.update(pill.get("sids", []))
        working = [
            self.sessions[sid] for sid in working_sids
            if sid in self.sessions
        ]
        if not working:
            if self._emote_visible:
                self.hide_emote()
            self._next_mood_check = 0
            return
        if self._emote_visible and now >= self._emote_hide_at:
            self.hide_emote()
        if not self._emote_visible and now >= self._next_mood_check:
            self._next_mood_check = now + random.uniform(12, 20)
            mood = None
            for sess in working:
                tp = sess.get("transcript")
                if tp:
                    mood = detect_mood(tp)
                    if mood:
                        break
            if mood:  # only show if the transcript actually expresses a mood
                duration = self._show_mood(mood, now)
                self._emote_hide_at = now + duration
        if self._emote_visible:
            self._update_emote_position()

    def _show_mood(self, mood, now):
        """Render the mood. 50% chance a text blurb instead of an emoji.
        Returns how long the emote should stay visible."""
        if random.random() < 0.5 and mood in MOOD_BLURBS:
            text = random.choice(MOOD_BLURBS[mood])
            self.show_emote_text(text)
            return 5.5  # text needs more time to read
        emoji_set = MOOD_EMOJI.get(mood, MOOD_EMOJI["thinking"])
        emoji = random.choice(emoji_set)
        self.show_emote(emoji)
        return 4.0

    def show_emote_text(self, text):
        """Render a small frosted text-bubble instead of an emoji."""
        self.emote_label.config(
            image="", text=text,
            bg=GLASS_FILL, fg=GLASS_FG,
            font=("Segoe UI", 9, "bold"),
            padx=dp(10), pady=dp(4),
        )
        self._update_emote_position()
        if not self._emote_visible:
            self.emote_window.deiconify()
            self.emote_window.attributes("-topmost", True)
            self._emote_visible = True

    def _update_emote_position(self):
        """Pin the emote window snug against the crab's upper-right corner."""
        try:
            self.root.update_idletasks()
            crab_screen_x = self.root.winfo_x() + self.crab_label.winfo_x()
            crab_screen_y = self.root.winfo_y() + self.crab_label.winfo_y()
            crab_w = self.crab_label.winfo_width()
            # Overlap slightly with the crab's right edge so the emoji sits
            # right next to the head instead of floating away.
            ex = crab_screen_x + crab_w - dp(16)
            ey = crab_screen_y + dp(2)
            self.emote_window.geometry(f"+{ex}+{ey}")
        except Exception:
            pass

    def show_emote(self, emoji):
        photo = self._get_emoji_photo(emoji)
        if photo is not None:
            self.emote_label.config(image=photo, text="", bg=TRANSPARENT_KEY,
                                    padx=0, pady=0)
        else:
            # Fallback: text on a soft chip so it stays readable without the
            # color-emoji image. Only kicks in if the emoji font is missing.
            self.emote_label.config(image="", text=emoji, bg=TAG_BG,
                                    padx=dp(8), pady=dp(2))
        self._update_emote_position()
        if not self._emote_visible:
            self.emote_window.deiconify()
            self.emote_window.attributes("-topmost", True)
            self._emote_visible = True

    def hide_emote(self):
        if self._emote_visible:
            self.emote_window.withdraw()
            self._emote_visible = False

    # ---------- shake to clear ----------

    def _auto_shake_clear(self):
        """Triggered after 10 min of no working activity. Crab wakes up
        (hop), wiggles side-to-side to shake itself off, then dismisses
        every ready/idle pill."""
        self._shake_anim_active = True
        self._hop_until = time.time() + 0.45  # the "wake up" hop first
        self.root.after(500, self._auto_shake_wiggle)

    def _auto_shake_wiggle(self):
        """Side-to-side wiggle by moving the crab's anchor so _reanchor
        actually applies it (otherwise reanchor snaps any direct geometry
        change right back)."""
        if not hasattr(self, "_anchor_center_x"):
            self._shake_anim_active = False
            return
        base = self._anchor_center_x
        offsets = [dp(v) for v in (6, -6, 5, -5, 4, -4, 3, -3, 0)]

        def step(i):
            if i < len(offsets):
                self._anchor_center_x = base + offsets[i]
                self._reanchor()
                self.root.after(55, lambda: step(i + 1))
            else:
                self._anchor_center_x = base
                self._reanchor()
                self._shake_clear_idle()
                self._shake_anim_active = False

        step(0)

    def _shake_clear_idle(self):
        """Dismiss every pill whose sessions are all idle/finished/ready.
        Active states (working / permission / attention / tool) are protected
        so a shake mid-task can't accidentally hide a working session.
        Each cleared pill flashes orange→red briefly before disappearing."""
        ACTIVE = {"working", "permission", "attention", "tool"}
        to_clear = []
        for label_key, pill in list(self.pills.items()):
            sids = pill.get("sids", [])
            if not any(
                self.sessions.get(sid, {}).get("state") in ACTIVE for sid in sids
            ):
                to_clear.append((label_key, sids))
        for label_key, sids in to_clear:
            self._flash_then_dismiss(label_key, sids)

    def _flash_then_dismiss(self, label_key, sids):
        """Briefly flash a pill orange→red, then dismiss its sessions."""
        pill = self.pills.get(label_key)
        if not pill:
            return
        label = pill["label"]

        def flash(color, fg, then_ms, after_fn):
            if label.winfo_exists():
                try:
                    label.config(bg=color, fg=fg)
                    # Repaint the glass card in the flash colour too, so the
                    # whole pill tints rather than just the text's backing.
                    self._refit_pill(pill, fill=color)
                except tk.TclError:
                    pass
            self.root.after(then_ms, after_fn)

        def finalize():
            for sid in sids:
                data = self.sessions.get(sid)
                if data:
                    self.dismissed[sid] = data.get("ts", 0)
            if label_key in self.pills:
                try:
                    self.pills[label_key]["frame"].destroy()
                except tk.TclError:
                    pass
                del self.pills[label_key]
            self._reanchor()

        flash("#ff9933", "white", 90,
              lambda: flash("#ff5555", "white", 90, finalize))

    # ---------- chime ----------

    def toggle_chime(self):
        self.settings["chime"] = not self.settings.get("chime", False)
        save_settings(self.settings)
        # Audible confirmation so Tian knows it took.
        if self.settings["chime"]:
            play_chime("cheer")

    def toggle_clamp_y(self):
        """Toggle the Y-clamp that keeps the crab above the Windows taskbar."""
        self.settings["clamp_y"] = not self.settings.get("clamp_y", False)
        save_settings(self.settings)
        if self.settings["clamp_y"]:
            self.show_toast("✓ Locked above taskbar")
            self._reanchor()  # pull up immediately if currently below
        else:
            self.show_toast("Free to overlap taskbar")

    def clear_sessions(self):
        for f in SESSIONS_DIR.glob("*.json"):
            try:
                f.unlink()
            except Exception:
                pass
        try:
            if LEGACY_STATUS_FILE.exists():
                LEGACY_STATUS_FILE.unlink()
        except Exception:
            pass

    def clear_always_allow(self):
        try:
            if ALWAYS_FILE.exists():
                ALWAYS_FILE.unlink()
        except Exception:
            pass

    # ---------- animation ----------

    def update_animation(self):
        iv = self.anim_intervals
        if self._dragged:
            self._play("dangle", self.dangle_tks, iv["dangle"])
            return
        if time.time() < self._cheer_until:
            self._play("cheer", self.cheer_tks, iv["cheer"])
            return
        if time.time() < self._hop_until:
            self._play("hop", self.hop_tks, iv["hop"])
            return
        # Drive the animation from the EXACT pills currently displayed, so
        # the crab can never disagree with what Tian sees. If every pill is
        # green (ready/finished is already filtered out of pills), the crab
        # sleeps. A working pill — and only a working pill — makes it type.
        visible_states = [p.get("state", "idle") for p in self.pills.values()]
        if "working" in visible_states:
            self._play("typing", self.typing_tks, iv["typing"])
        elif any(s in ("attention", "permission", "tool") for s in visible_states):
            # Crab: static stance. Crow: animated alert idle (looks around).
            self._play("standing", self.standing_tks, iv["standing"])
        else:
            # Sleeping with the new cap + Zzz — the idle state.
            self._play("sleeping", self.sleeping_tks, iv["sleeping"])

    def _play(self, name, frames, interval_ms):
        if name == self._current_anim:
            return
        self._current_anim = name
        if self._anim_after is not None:
            self.root.after_cancel(self._anim_after)
            self._anim_after = None
        if frames is None:
            self.crab_label.config(image=self.standing_tk)
            return
        if isinstance(frames, list):
            self._anim_frames = frames
            self._anim_interval = interval_ms
            self._anim_idx = 0
            self._tick_anim()
        else:
            # Single static PhotoImage (e.g. a single-frame PNG override).
            self.crab_label.config(image=frames)

    def _tick_anim(self):
        self.crab_label.config(image=self._anim_frames[self._anim_idx])
        self._anim_idx = (self._anim_idx + 1) % len(self._anim_frames)
        self._anim_after = self.root.after(self._anim_interval, self._tick_anim)

    # ---------- poll ----------

    def load_sessions(self):
        out = {}
        now = time.time()
        for f in SESSIONS_DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text())
                sid = f.stem
                # Strip legacy junk labels (file drops, Caveats, system tags
                # left over from when the hook used the first user prompt as
                # a fallback). Rewrite the file so they stay gone.
                if _is_garbage_label(data.get("label")):
                    data.pop("label", None)
                    try:
                        f.write_text(json.dumps(data))
                    except Exception:
                        pass
                # If we know where the transcript is, refresh the label from it
                # so user renames (custom-title) appear without waiting for a
                # new hook to fire. Throttled to once every 5s per session.
                transcript = data.get("transcript")
                if transcript and now - self._label_check_ts.get(sid, 0) > 5:
                    fresh = extract_label_from_transcript(transcript)
                    self._label_check_ts[sid] = now
                    changed = False
                    if fresh:
                        if fresh != data.get("label"):
                            data["label"] = fresh
                            changed = True
                    elif data.get("label"):
                        # No real custom/ai title in the transcript — drop
                        # any stale prompt-derived label that's still
                        # hanging around from the old fallback behavior.
                        data.pop("label", None)
                        changed = True
                    if changed:
                        try:
                            f.write_text(json.dumps(data))
                        except Exception:
                            pass
                out[sid] = data
            except Exception:
                pass
        # Merge in live sessions from the Claude Desktop registry — this
        # picks up Chat/Cowork sessions that don't fire UserPromptSubmit
        # hooks. "Working" is inferred from transcript growth.
        live_sids = self._merge_live_sessions(out, now)

        # Zombie sweep: a session can only legitimately be "working" if a
        # live process backs it (in the registry) OR its hook fired very
        # recently (< 90s). Otherwise it's a dead session whose Stop hook
        # never ran — demote stale "working" to "ready" so the crab doesn't
        # type for a ghost. THIS is what kept the crab falsely busy.
        for sid, data in out.items():
            backed = sid in live_sids
            data["live"] = backed
            if data.get("state") == "working":
                recent_hook = (now - data.get("ts", 0)) < 90
                if not backed and not recent_hook:
                    data["state"] = "ready"

        # User-assigned names win over any transcript-derived title.
        if self.session_names:
            for sid, data in out.items():
                name = self.session_names.get(sid)
                if name:
                    data["label"] = name

        # Fallback: legacy single-session file.
        if not out and LEGACY_STATUS_FILE.exists():
            try:
                out["legacy"] = json.loads(LEGACY_STATUS_FILE.read_text())
            except Exception:
                pass
        return out

    def _load_session_names(self):
        try:
            data = json.loads(SESSION_NAMES_FILE.read_text())
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_session_names(self):
        try:
            SESSION_NAMES_FILE.write_text(json.dumps(self.session_names))
        except Exception:
            pass

    def _merge_live_sessions(self, out, now):
        """Scan ~/.claude/sessions/*.json (live registry written by Claude
        Desktop for every running session) and surface them in the crab.
        If the session's transcript was modified in the last ~6s, mark as
        working — this is how we detect Chat/Cowork activity since those
        modes don't reliably fire the UserPromptSubmit hook.

        Returns the set of session ids that have a live (running) process,
        used by the zombie sweep to demote dead "working" states."""
        live_sids = set()
        if not LIVE_SESSIONS_DIR.exists():
            return live_sids
        for f in LIVE_SESSIONS_DIR.glob("*.json"):
            try:
                live = json.loads(f.read_text())
            except Exception:
                continue
            sid = live.get("sessionId")
            cwd = live.get("cwd")
            if not sid or not cwd:
                continue
            # Registry files aren't cleaned up on crash and never heartbeat,
            # so verify the PID is actually running. A dead PID means a stale
            # file — skip it (it won't enter live_sids, so the zombie sweep
            # will demote any lingering "working" hook state for it).
            if not _pid_alive(live.get("pid")):
                continue
            live_sids.add(sid)
            transcript = _derive_transcript_path(cwd, sid)
            if transcript is None or not transcript.exists():
                continue
            try:
                st = transcript.stat()
                mtime = st.st_mtime
                size = st.st_size
            except Exception:
                continue
            # Real activity = a NEW user/assistant/thinking line got
            # appended. Claude Desktop also writes queue-operation, mode,
            # last-prompt, ai-title metadata lines on idle — those grew
            # the file but don't mean Claude is doing anything. We peek at
            # the appended bytes and only trigger working on real message
            # types.
            prev_size = self._last_size.get(sid)
            self._last_size[sid] = size
            fresh = False
            if prev_size is not None and size > prev_size:
                try:
                    with open(transcript, "rb") as fh:
                        fh.seek(prev_size)
                        chunk = fh.read(min(size - prev_size, 200_000))
                    if (b'"type":"user"' in chunk
                            or b'"type":"assistant"' in chunk
                            or b'"type":"thinking"' in chunk):
                        fresh = (now - mtime) < 25.0
                except Exception:
                    pass
            # Once we see a real append, extend a short grace window for
            # thinking / tool-call gaps where the transcript sits idle
            # but Claude is still working. Kept tight so the crab settles
            # quickly once a turn actually ends.
            if fresh:
                self._inferred_active_until[sid] = now + 20.0
            inferred_active = now < self._inferred_active_until.get(sid, 0)
            existing = out.get(sid, {})
            ex_state = existing.get("state")
            # This session is LIVE (it's in the registry). So if its last
            # hook said "working" and no Stop hook has since marked it
            # finished, trust that for the WHOLE turn — Claude can think for
            # minutes without touching the transcript, and we must not fall
            # asleep mid-think. A dead session can't reach here (the zombie
            # sweep demotes those), and the Stop hook flips this to
            # "finished" the instant the turn actually ends.
            hook_working = (ex_state == "working")
            if ex_state == "finished":
                state = "finished"  # Stop hook fired — definitive done
                self._inferred_active_until.pop(sid, None)
            elif ex_state in ("attention", "permission", "tool"):
                state = ex_state  # user-actionable signals always win
            elif fresh or inferred_active or hook_working:
                state = "working"
            else:
                state = "ready"
            label = existing.get("label")
            if _is_garbage_label(label):
                label = None
            if not label and now - self._label_check_ts.get(sid, 0) > 5:
                label = extract_label_from_transcript(str(transcript))
                self._label_check_ts[sid] = now
            # If a working session still has no real title yet, show a
            # clean placeholder so it has a visible pill — but only while
            # working, not for unlabeled ready sessions.
            if not label and state == "working":
                label = "new chat"
            merged = {
                "state": state,
                "ts": max(existing.get("ts", 0), mtime),
                "transcript": str(transcript),
            }
            if label:
                merged["label"] = label
            out[sid] = merged
        return live_sids

    def poll(self):
        self.sessions = self.load_sessions()

        # Detect state transitions:
        #   * → "finished"  → cheer for 2 sec (+ optional chime)
        #   * → "working"   → small hop for ~0.4 sec
        # Only labeled, non-dismissed sessions trigger animations — zombie
        # unlabeled sessions (e.g. a Claude that was force-killed) shouldn't
        # be making the crab type or cheer when no pill is visible.
        now = time.time()
        for sid, data in self.sessions.items():
            curr = data.get("state")
            prev = self._prev_states.get(sid)
            self._prev_states[sid] = curr
            if not data.get("label") or sid in self.dismissed:
                continue
            if prev and prev != curr:
                if curr == "finished":
                    self._cheer_until = now + 2.0
                    if self.settings.get("chime"):
                        play_chime("cheer")
                elif curr == "working":
                    self._hop_until = now + 0.4
        for sid in list(self._prev_states):
            if sid not in self.sessions:
                del self._prev_states[sid]

        # Track last working-activity for the 10-min auto-shake-clear.
        has_working = any(
            d.get("state") == "working" and d.get("label")
            and sid not in self.dismissed
            for sid, d in self.sessions.items()
        )
        if has_working:
            self._last_activity_ts = now
        else:
            has_clearable = any(
                d.get("label") and sid not in self.dismissed
                and d.get("state") not in
                    ("working", "attention", "permission", "tool", "finished")
                for sid, d in self.sessions.items()
            )
            if (has_clearable
                    and now - self._last_activity_ts > 600
                    and now > self._auto_clean_cooldown_until
                    and not self._shake_anim_active
                    and not self._dragged):
                self._auto_clean_cooldown_until = now + 1800  # 30-min cooldown
                self._auto_shake_clear()

        # Build the pills FIRST so the emote + animation both read from the
        # exact same displayed-pill state (no phantom working).
        self.update_pills(self.sessions)

        # Mood emote: while a working pill is shown, sample its transcript
        # every ~15s and float a tiny emoji thought-bubble above the crab.
        self._update_emote(now)

        pending_now = PENDING_FILE.exists() and not RESPONSE_FILE.exists()
        if pending_now:
            try:
                pending = json.loads(PENDING_FILE.read_text())
                self.show_bubble(pending)
            except Exception:
                self.hide_bubble()
        else:
            self.hide_bubble()
        # Chime on the rising edge of a pending permission.
        if pending_now and not self._prev_pending and self.settings.get("chime"):
            play_chime("attention")
        self._prev_pending = pending_now

        # MCP server needs reauth?
        self._check_mcp_reauth()

        self.update_animation()
        self._reanchor()
        self._update_pill_window()
        self.root.after(300, self.poll)

    def _force_topmost(self):
        """Re-assert HWND_TOPMOST on the crab window AND on any visible
        auxiliary windows (emote / delete / toast). When another topmost app
        gets re-asserted Windows can squeeze it between our windows in
        z-order — we fight back by raising all of ours every tick.
        Uses wm_frame() to get the real toplevel HWND (winfo_id can return a
        child HWND for overrideredirect windows)."""
        user32 = ctypes.windll.user32
        GWL_EXSTYLE = -20
        WS_EX_TOPMOST = 0x00000008

        def bump(window):
            try:
                frame_id = window.wm_frame()
                if isinstance(frame_id, str) and frame_id.startswith("0x"):
                    hwnd = int(frame_id, 16)
                else:
                    hwnd = int(frame_id)
                ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                if not (ex & WS_EX_TOPMOST):
                    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex | WS_EX_TOPMOST)
                user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0,
                                    0x0001 | 0x0002 | 0x0010)
            except Exception:
                pass

        # Is the right-click menu open right now? If so we deliberately don't
        # bump the crab — otherwise the 40ms loop keeps stomping over the
        # menu and buries it. We only re-raise the menu while it's up.
        menu = getattr(self, "_active_menu", None)
        menu_open = False
        if menu is not None:
            try:
                menu_open = bool(menu.winfo_exists() and menu.winfo_viewable())
            except Exception:
                menu_open = False

        if not menu_open:
            try:
                self.root.lift()
            except Exception:
                pass
            bump(self.root)

        # Aux windows don't compete with the menu's position, so it's safe to
        # keep re-asserting them either way.
        if getattr(self, "_pill_window_visible", False):
            bump(self.pill_window)
        if getattr(self, "_usage_visible", False):
            bump(self._usage_window)
        if getattr(self, "_emote_visible", False):
            bump(self.emote_window)
        if getattr(self, "_delete_visible", False):
            bump(self.delete_window)
        if getattr(self, "_mcp_visible", False):
            bump(self.mcp_window)
        toast = getattr(self, "_toast_window", None)
        if toast is not None:
            try:
                if toast.winfo_viewable():
                    bump(toast)
            except Exception:
                pass

        # Menu always last when open — guarantees it's at the top of the
        # topmost group, above the crab and the aux windows.
        if menu_open:
            bump(menu)

        # The rename entry box must stay above the crab while it's open.
        rename = getattr(self, "_rename_window", None)
        if rename is not None:
            try:
                if rename.winfo_exists():
                    bump(rename)
            except Exception:
                pass

    def _topmost_loop(self):
        """Fast loop (~25x/sec): re-assert topmost AND sync auxiliary bubble
        positions so they follow the crab in real time during drag."""
        self._force_topmost()
        if self._pill_window_visible:
            self._update_pill_window()  # follow the crab during drag
        if self._usage_visible:
            self._update_usage_position()
        if self._emote_visible:
            self._update_emote_position()
        if self._mcp_visible:
            self._update_mcp_position()
        self.root.after(40, self._topmost_loop)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    # First-run: generate the crab .ico and point existing shortcuts at it,
    # so they don't show the default Python icon anymore.
    if not ICON_PATH.exists():
        if generate_icon():
            update_shortcut_icons()
    CrabPet().run()
