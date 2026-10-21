# 🦀 Claude Crab — a tiny desktop pet for Claude Code

A pixel-art crab that lives in the corner of your desktop and shows what
Claude is doing — so you don't have to keep the terminal open.

> ⚠️ **Windows only.** The crab relies on Win32 APIs (always-on-top window
> layering, transparent-color keying), `winsound` for the chime, and a
> PowerShell installer. It does **not** run on macOS or Linux.

## Install

Run `install.ps1` (right-click → "Run with PowerShell"). It checks Python,
installs Pillow, copies the pet to `~/.claude/pet/`, merges hooks into
`~/.claude/settings.json`, creates Startup + Start Menu shortcuts with the
crab icon, and launches the crab. See **SHARE.md** for the full coworker
walkthrough.

## What it does

- 🐦 **Crow mode** — right-click → *Switch to crow* swaps the pixel crab for a
  smooth vector crow with its own animations: pecks at a laptop while Claude
  works, naps in a nest with a nightcap, flaps and caws when a task finishes,
  panics when you pick it up. Choice is remembered per pet.
- 📊 **Usage panel** — click the pet to see your current 5-hour session
  (progress + reset countdown, tokens used) and a 7-day usage chart. Computed
  locally from Claude Code transcripts; no network, no credentials.
- 🦀 **Pixel-art crab** sits on top of every window, transparent background,
  drag to move (including to other monitors).
- 🏷️ **Dark status pills** above the crab show one row per Claude session,
  named after the chat title (or Claude's AI-generated title if you haven't
  renamed it). Same-name sessions merge into one pill with the most active
  state.
- 🎬 **Animations:**
  - **Standing** when sessions are alert (ready / attention / permission)
  - **Typing** when any session is working — body holds steady, the 4 legs
    cycle one-lifted-at-a-time, lit key tracks the tapping leg
  - **Sleeping** when nothing's active — droopy nightcap (pastel blue + white
    pom-pom), eyes closed, Zzz floats up
  - **Dangling** while you drag — bottom legs swing pendulum-style
  - **Cheering** 2s after any session finishes — happy closed-eye smile +
    confetti shower in 5 colors
- 🧠 **Mood emoji** floats next to the crab while working: 😊 / 🤔 / 💭 / 😤 /
  🤬 / 😔 depending on what the assistant has been saying recently (scans
  the transcript JSONL for sentiment cues).
- 🔔 **Optional chime** — soft Windows beep on cheer / permission popup
- 💬 **Speech bubble** when Claude wants permission — `Yes` / `Always` / `No`
- 🦴 **Shake to clear** — wiggle the crab side to side fast to dismiss all
  idle/done pills (active sessions are protected). Pills flash orange→red as
  they fly off.

## Right-click menu

- **Open Claude** — focuses Claude Desktop, or launches it
- **📊 Usage** — open the usage panel (same as clicking the pet)
- **🐦 Switch to crow / 🦀 Switch to crab** — change character
- **🎉 Cheer** — fire the cheer animation on demand
- **✨ Duplicate crab** — spawn another crab with its own position
- **🔔 Enable chime / 🔇 Mute chime** — toggle the sound effect
- **Lock above taskbar / Allow taskbar overlap** — toggle the Y-clamp
- **Clear sessions** — wipe the pill list
- **Clear always-allow** — reset the auto-allowed tools
- **Enable/Disable autostart** — toggle the login-startup shortcut
- **Quit crab** — close this crab instance

## Hovering a pill

A small floating **−** button appears just to the right. Click it to dismiss
that pill (and all sessions sharing its name). The minus stays out of the
pill's bounding box so the pill never resizes on hover.

## Permission gate

A `PreToolUse` hook is wired to `Bash` by default. When Claude tries to run a
Bash command, the crab shows a speech bubble with:

- **Yes** → allow this one call
- **Always** → allow + remember in `state/always_allow.json` (skips the bubble
  on future calls to that tool)
- **No** → deny; Claude is told the user rejected it

To broaden the gate to other tools, change `"matcher": "Bash"` in your
`~/.claude/settings.json` to e.g. `"Bash|Write|Edit"`.

## Files

| File | What |
|---|---|
| `pet.py` | The desktop pet (Tk + Pillow) |
| `crow.py` | Procedural vector crow: poses → smooth animation frames (cached in `state/crow_frames/`) |
| `usage.py` | Incremental transcript scanner behind the usage panel (index in `state/usage_index.json`) |
| `hook_status.py` | Writes per-session status from Claude hooks |
| `permission_gate.py` | PreToolUse gate that pauses Claude until you click |
| `start-crab.vbs` | Silent launcher (no console window) |
| `start-crab.bat` | Visible launcher (useful for debugging) |
| `install.ps1` | One-click installer for new machines |
| `state/sessions/<id>.json` | Per-Claude-session state, kept fresh by hooks |
| `state/settings.json` | Pet preferences (chime, Y-clamp, etc.) |
| `state/always_allow.json` | Tools you've granted "always" permission for |
| `state/position.json` | Where the crab is parked |
| `state/crab.ico` | The crab icon used by Start Menu / Startup shortcuts |
| `state/gate.log` | Permission-gate debug log |

## Requirements

- **Windows 10 / 11 only** — not compatible with macOS or Linux
- Python 3.8+ (any modern install)
- Pillow (the installer pip-installs it)
- Claude Code

## Uninstall

Delete `~/.claude/pet/`, the two `.lnk` files in your Startup and Start Menu,
and the hook entries from `~/.claude/settings.json`.
