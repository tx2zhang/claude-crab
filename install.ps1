# install.ps1 — One-click installer for the Claude Crab desktop pet.
# Coworkers run this once (right-click → "Run with PowerShell").
#
# What it does:
#   1. Verifies Python is installed; installs Pillow if missing.
#   2. Copies pet files to %USERPROFILE%\.claude\pet\
#   3. Adds Claude Code hooks (SessionStart / UserPromptSubmit / Stop /
#      Notification + a PreToolUse gate on Bash) to %USERPROFILE%\.claude\settings.json
#   4. Creates a Startup-folder shortcut so the crab autolaunches at login.
#   5. Creates a Start Menu shortcut you can right-click → Pin to Taskbar.
#   6. Launches the crab.

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "  🦀 Installing Claude Crab..." -ForegroundColor Cyan
Write-Host ""

# --- 1. Locate Python ---
$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) {
    Write-Host "  ✗ Python isn't installed. Get it from https://python.org first." -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}
$realPython = (& python -c "import sys; print(sys.executable)").Trim()
$pythonDir = Split-Path $realPython
$pythonw = Join-Path $pythonDir "pythonw.exe"
if (-not (Test-Path $pythonw)) { $pythonw = $realPython }

Write-Host "  Python found at: $realPython"

# --- 2. Pillow ---
Write-Host "  Installing Pillow..."
& $realPython -m pip install --quiet --upgrade Pillow | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "  ⚠ Pillow install returned non-zero, continuing..." -ForegroundColor Yellow
}

# --- 3. Set up pet directory ---
$petDir = Join-Path $env:USERPROFILE ".claude\pet"
$stateDir = Join-Path $petDir "state"
$sessionsDir = Join-Path $stateDir "sessions"
New-Item -ItemType Directory -Force -Path $petDir, $stateDir, $sessionsDir | Out-Null

$scriptDir = $PSScriptRoot
foreach ($file in @("pet.py", "hook_status.py", "permission_gate.py", "README.md")) {
    $src = Join-Path $scriptDir $file
    if (Test-Path $src) {
        Copy-Item $src $petDir -Force
    }
}

# --- 4. Generate machine-specific launchers ---
$vbsBody = "Set sh = CreateObject(""WScript.Shell"")`r`n" +
           "sh.CurrentDirectory = ""$petDir""`r`n" +
           "sh.Run """"""$pythonw"""" pet.py"", 0, False`r`n"
$vbsBody | Out-File -Encoding ASCII -FilePath (Join-Path $petDir "start-crab.vbs") -NoNewline

$batBody = "@echo off`r`ncd /d ""%~dp0""`r`n""$realPython"" pet.py`r`n"
$batBody | Out-File -Encoding ASCII -FilePath (Join-Path $petDir "start-crab.bat") -NoNewline

# --- 5. Merge Claude Code hooks into settings.json ---
$settingsPath = Join-Path $env:USERPROFILE ".claude\settings.json"
$settingsObj = [ordered]@{}
if (Test-Path $settingsPath) {
    try {
        $existing = Get-Content $settingsPath -Raw | ConvertFrom-Json
        foreach ($prop in $existing.PSObject.Properties) {
            $settingsObj[$prop.Name] = $prop.Value
        }
    } catch {
        Write-Host "  ⚠ Couldn't parse existing settings.json, starting fresh" -ForegroundColor Yellow
    }
}

$hookCmd = "`"$pythonw`" `"$(Join-Path $petDir 'hook_status.py')`""
$gateCmd = "`"$realPython`" `"$(Join-Path $petDir 'permission_gate.py')`""

function New-CrabHookEntry($state) {
    [PSCustomObject]@{
        hooks = @([PSCustomObject]@{
            type = "command"
            command = "$hookCmd $state"
        })
    }
}

$hookMap = [ordered]@{
    "SessionStart"     = "ready"
    "UserPromptSubmit" = "working"
    "Stop"             = "finished"
    "Notification"     = "attention"
}

if (-not $settingsObj.hooks) {
    $settingsObj["hooks"] = [PSCustomObject]@{}
} elseif ($settingsObj.hooks -isnot [PSCustomObject]) {
    $settingsObj["hooks"] = [PSCustomObject]$settingsObj.hooks
}

foreach ($evt in $hookMap.Keys) {
    $entry = New-CrabHookEntry $hookMap[$evt]
    if ($settingsObj.hooks.PSObject.Properties.Name -contains $evt) {
        $arr = @($settingsObj.hooks.$evt)
        $hasCrab = $false
        foreach ($e in $arr) {
            foreach ($h in $e.hooks) {
                if ($h.command -like "*hook_status.py*") { $hasCrab = $true; break }
            }
            if ($hasCrab) { break }
        }
        if (-not $hasCrab) {
            $settingsObj.hooks.$evt = $arr + @($entry)
        }
    } else {
        $settingsObj.hooks | Add-Member -NotePropertyName $evt -NotePropertyValue @($entry)
    }
}

# PreToolUse gate on Bash
$gateEntry = [PSCustomObject]@{
    matcher = "Bash"
    hooks = @([PSCustomObject]@{
        type    = "command"
        command = $gateCmd
        timeout = 600
    })
}
if ($settingsObj.hooks.PSObject.Properties.Name -contains "PreToolUse") {
    $arr = @($settingsObj.hooks.PreToolUse)
    $hasCrab = $false
    foreach ($e in $arr) {
        foreach ($h in $e.hooks) {
            if ($h.command -like "*permission_gate.py*") { $hasCrab = $true; break }
        }
        if ($hasCrab) { break }
    }
    if (-not $hasCrab) {
        $settingsObj.hooks.PreToolUse = $arr + @($gateEntry)
    }
} else {
    $settingsObj.hooks | Add-Member -NotePropertyName "PreToolUse" -NotePropertyValue @($gateEntry)
}

$settingsObj | ConvertTo-Json -Depth 100 | Out-File -Encoding utf8 -FilePath $settingsPath

# --- 6. Pre-generate icon by importing pet.py ---
Write-Host "  Generating crab icon..."
$genCode = "import sys; sys.path.insert(0, r'$petDir'); from pet import generate_icon; generate_icon()"
& $realPython -c $genCode 2>$null | Out-Null

$iconPath = Join-Path $stateDir "crab.ico"

# --- 7. Shortcuts ---
$startupLnk  = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup\ClaudeCrab.lnk"
$startMenuLnk = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Claude Crab.lnk"
$vbsPath     = Join-Path $petDir "start-crab.vbs"

$w = New-Object -ComObject WScript.Shell
foreach ($lnk in @($startupLnk, $startMenuLnk)) {
    $s = $w.CreateShortcut($lnk)
    $s.TargetPath = "wscript.exe"
    $s.Arguments  = "`"$vbsPath`""
    $s.WorkingDirectory = $petDir
    $s.Description = "Claude Code desktop crab pet"
    if (Test-Path $iconPath) {
        $s.IconLocation = "$iconPath,0"
    }
    $s.Save()
}

# --- 8. Launch ---
Write-Host ""
Write-Host "  ✓ Installed!" -ForegroundColor Green
Write-Host "  Look for the crab in the bottom-right of your screen."
Write-Host "  Right-click on it for the menu."
Write-Host ""

Start-Process wscript.exe -ArgumentList "`"$vbsPath`""
