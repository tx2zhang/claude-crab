# Sharing the Claude Crab with coworkers

The whole pet lives in `~/.claude/pet/` — to share it, zip that folder up and
send it along with these instructions.

## Quick share (Windows → Windows)

1. **Zip your pet folder.** Open `%USERPROFILE%\.claude\pet` in Explorer,
   select all the files, right-click → "Compress to ZIP file". Name it
   `claude-crab.zip`.
2. **Send the zip** (email, Slack, OneDrive, GitHub gist, whatever).
3. **Your coworker unzips it anywhere** (e.g. their Downloads folder).
4. **They right-click `install.ps1` → "Run with PowerShell"**.

That's it. The installer:
- Checks they have Python and installs Pillow.
- Copies the pet files to their `%USERPROFILE%\.claude\pet\`.
- Adds the Claude Code hooks to their `settings.json` (preserves any existing hooks).
- Creates the Startup-folder autostart shortcut.
- Creates a Start Menu shortcut they can pin to taskbar.
- Generates the crab icon and points the shortcuts at it.
- Launches the crab.

If PowerShell blocks the script with "execution policy" complaints, they can
run this once in PowerShell:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

## Requirements your coworker needs

- **Windows 10 / 11**
- **Python 3.8+** (anything modern; `winsound` and `tk` are stdlib)
- Claude Code installed (the pet is integrated via Claude's hooks)

Pillow is installed automatically by the installer if missing.

## What gets installed where

| Path | What |
|---|---|
| `%USERPROFILE%\.claude\pet\` | All pet scripts + state |
| `%USERPROFILE%\.claude\settings.json` | Adds 5 hook entries |
| `%APPDATA%\...\Startup\ClaudeCrab.lnk` | Autostart at login |
| `%APPDATA%\...\Programs\Claude Crab.lnk` | Start Menu shortcut |

## Uninstall

Delete `%USERPROFILE%\.claude\pet\`, the two `.lnk` shortcuts, and remove the
hook entries from `settings.json`.
