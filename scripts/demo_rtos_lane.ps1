# KINETIQ LANE B (FreeRTOS node -> mirror) — single entry point, run from repo root:
#   powershell -ExecutionPolicy Bypass -File scripts\demo_rtos_lane.ps1
# No phone, no receiver, no network: fully deterministic, works offline.
# Exit code = number of FAILs (WARN never fails).
#
# Chain started (one window, its stderr visible on screen):
#   hardware\native\kinetiq_native.exe --run-seconds 300
#     | laptop\web\app.py --stdin --lane rtos
#
# The native diag line ("[kinetiq] --run-seconds 300 elapsed, exiting") is on stderr and is
# deliberately NOT redirected: it must stay readable in that window.

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root

$NativeExe = Join-Path $Root "hardware\native\kinetiq_native.exe"
$BuildPs1  = Join-Path $Root "hardware\native\build.ps1"
$App       = Join-Path $Root "laptop\web\app.py"
$VenvPy    = Join-Path $Root "laptop\.venv\Scripts\python.exe"
$RunSeconds = 300

$script:Fails = 0
$script:Warns = 0

function Record([string]$Name, [string]$Result, [string]$Evidence) {
    Write-Host ""
    Write-Host "[$Result] $Name -- $Evidence"
    if ($Result -eq "FAIL") { $script:Fails++ }
    if ($Result -eq "WARN") { $script:Warns++ }
}

function Abort([string]$Name, [string]$Where, [string]$Fix) {
    Write-Host ""
    Write-Host "[FAIL] $Name -- $Where"
    Write-Host "  FIX: $Fix"
    Write-Host ""
    Write-Host "LANE B was NOT started."
    exit 1
}

function Test-PortListening([int]$P) {
    try {
        if (@(Get-NetTCPConnection -LocalPort $P -State Listen -ErrorAction Stop).Count -gt 0) { return $true }
        return $false
    } catch {
        $hits = @(netstat -ano 2>$null | Select-String -Pattern (":{0}\s+\S+\s+LISTENING" -f $P))
        return ($hits.Count -gt 0)
    }
}

# One visible console window running a cmd pipeline (same pattern as KINETIQ_DEMO.bat's
# hero lane). cmd is used instead of `powershell -NoExit -Command`: raw bytes on the pipe
# (no re-encoding), stderr of BOTH sides stays on screen, and the title sticks.
function Start-LaneWindow([string]$Title, [string]$Pipeline, [string]$Tag) {
    $tmpBase = Join-Path ([System.IO.Path]::GetTempPath()) "opencode"
    if (-not (Test-Path -LiteralPath $tmpBase)) { New-Item -ItemType Directory -Path $tmpBase -Force | Out-Null }
    $bat = Join-Path $tmpBase ("kinetiq_{0}.cmd" -f $Tag)
    $lines = @(
        "@echo off",
        ("title {0}" -f $Title),
        ("cd /d ""{0}""" -f $Root),
        $Pipeline,
        "echo.",
        "echo [lane] pipeline ended -- window kept open so the last lines stay readable.",
        "pause >nul"
    )
    Set-Content -LiteralPath $bat -Value $lines -Encoding ASCII
    Start-Process -FilePath "cmd.exe" -ArgumentList "/c", ('"{0}"' -f $bat) | Out-Null
    return $bat
}

Write-Host "================ KINETIQ LANE B (RTOS CONTROL) ================"

# ---------- 1. Python (hard) ----------
$Py = $null
$pyNote = ""
if (Test-Path -LiteralPath $VenvPy) { $Py = $VenvPy; $pyNote = "project venv laptop\.venv" }
else {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { $Py = $cmd.Source; $pyNote = "python on PATH (laptop\.venv absent)" }
}
if (-not $Py) {
    Abort "1. Python" "no laptop\.venv\Scripts\python.exe and no 'python' on PATH" `
        "create the venv (py -3 -m venv laptop\.venv; laptop\.venv\Scripts\python -m pip install -r laptop\requirements.txt) or put python on PATH."
}
$pyVer = (& $Py --version 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0) {
    Abort "1. Python" ("'$Py' did not run: $pyVer") "repair/recreate laptop\.venv, then re-run."
}
Record "1. Python" "PASS" "$pyVer ($pyNote)"

# ---------- 2. Mirror script (hard) ----------
if (-not (Test-Path -LiteralPath $App)) {
    Abort "2. Mirror script" "laptop\web\app.py not found" "run this from the repo that contains laptop\web\app.py (scripts\ sits at the repo root)."
}
Record "2. Mirror script" "PASS" "laptop\web\app.py present"

# ---------- 3. Native node: rebuild only if the exe is missing ----------
if (Test-Path -LiteralPath $NativeExe) {
    $sz = (Get-Item -LiteralPath $NativeExe).Length
    Record "3. Native node" "PASS" ("kinetiq_native.exe present, {0:N0} bytes (not rebuilt)" -f $sz)
} else {
    Write-Host ""
    Write-Host "[build] kinetiq_native.exe missing -> running hardware\native\build.ps1 ..."
    if (-not (Test-Path -LiteralPath $BuildPs1)) {
        Abort "3. Native node" "kinetiq_native.exe missing AND hardware\native\build.ps1 missing" `
            "restore the hardware\native sources (build.ps1 + main.c + kinetiq_tasks.c + freertos-kernel) from git: 'git checkout -- hardware\native'."
    }
    & powershell -ExecutionPolicy Bypass -File $BuildPs1
    if (-not (Test-Path -LiteralPath $NativeExe)) {
        Abort "3. Native node" "build.ps1 ran but kinetiq_native.exe is still missing" `
            "read the build output above (usually a missing gcc / winlibs toolchain on PATH). Fix the toolchain, re-run scripts\demo_rtos_lane.ps1; or run hardware\native\build.ps1 by hand."
    }
    $sz = (Get-Item -LiteralPath $NativeExe).Length
    Record "3. Native node" "PASS" ("rebuilt kinetiq_native.exe, {0:N0} bytes" -f $sz)
}

# ---------- 4. Mirror port ----------
$appPort = $null
if (Test-PortListening 8777) { $appPort = 8777 }
elseif (Test-PortListening 8778) { $appPort = 8778 }
if ($appPort) {
    Record "4. Mirror port" "WARN" "port $appPort is already in use -- app.py will pick the other one (8777/8778) or fail if both are busy"
} else {
    Record "4. Mirror port" "PASS" "8777/8778 free"
}

# ---------- 5. Start the lane (own window; stderr visible) ----------
Write-Host ""
Write-Host ('[run] "{0}" --run-seconds {1} | "{2}" "{3}" --stdin --lane rtos' -f $NativeExe, $RunSeconds, $Py, $App)
$pipeline = '"{0}" --run-seconds {1} | "{2}" "{3}" --stdin --lane rtos' -f $NativeExe, $RunSeconds, $Py, $App
$before = @(8777, 8778 | Where-Object { Test-PortListening $_ })
$bat = Start-LaneWindow "KINETIQ LANE B - RTOS to mirror" $pipeline "lane_b"
Write-Host ('[lane window] {0}' -f $bat)

$bound = $null
for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Milliseconds 500
    if ($before -notcontains 8777 -and (Test-PortListening 8777)) { $bound = 8777; break }
    if ($before -notcontains 8778 -and (Test-PortListening 8778)) { $bound = 8778; break }
}
if (-not $bound) {
    foreach ($p in @(8777, 8778)) { if ($before -contains $p) { $bound = $p; break } }
}
if ($bound -and ($before -notcontains $bound)) {
    Record "5. Mirror" "PASS" "http://127.0.0.1:$bound/  (lane=rtos, stdin source)"
    Start-Process ("http://127.0.0.1:{0}/" -f $bound) | Out-Null
} elseif ($bound) {
    Record "5. Mirror" "WARN" "port $bound was already serving a mirror before this run -- reusing that page; close the old window to bind a fresh one"
    Start-Process ("http://127.0.0.1:{0}/" -f $bound) | Out-Null
} else {
    Record "5. Mirror" "WARN" "mirror did not bind 8777/8778 within 20s -- read the LANE B window for the error"
}

# ---------- 6. Feed-rate probe (informational, never fails the run) ----------
# The node emits 1 packet/s; this measures what actually reached the mirror, so a slow or
# stuck pipe is visible here instead of being discovered on stage.
if ($bound) {
    try {
        $uri = "http://127.0.0.1:$bound/api/telemetry"
        $a = (Invoke-WebRequest -Uri $uri -UseBasicParsing -TimeoutSec 5).Content | ConvertFrom-Json
        Start-Sleep -Seconds 6
        $b = (Invoke-WebRequest -Uri $uri -UseBasicParsing -TimeoutSec 5).Content | ConvertFrom-Json
        $delta = [int]$b.freshness.arrivals - [int]$a.freshness.arrivals
        if ($delta -ge 4) {
            Record "6. Feed rate" "PASS" ("{0} packets in 6s; state={1} link={2} hr={3}" -f $delta, $b.freshness.state, $b.link.state, $b.latest.heart_rate)
        } elseif ($delta -ge 1) {
            Record "6. Feed rate" "WARN" ("only {0} packet(s) in 6s (expected ~6) -- read the LANE B window; if that window has no visible console, run {1} by hand from a normal terminal" -f $delta, $bat)
        } else {
            Record "6. Feed rate" "WARN" ("no packet in 6s (state={0}) -- read the LANE B window. If that window has no visible console, run {1} from a normal terminal window instead" -f $b.freshness.state, $bat)
        }
    } catch {
        Record "6. Feed rate" "WARN" ("could not read the mirror API: {0}" -f $_.Exception.Message)
    }
}

# ---------- Operator card ----------
Write-Host ""
Write-Host "================ LANE B CARD ================"
Write-Host "  LANE B is the deterministic control -- same contract, kernel timing measured."
Write-Host ("  Runs {0}s then stops on its own (diag line on stderr in the LANE B window)." -f $RunSeconds)
Write-Host "  No phone, no receiver, no network: if LANE A is dead on stage, this still paints."
Write-Host ""
Write-Host ("================ LANE B UP: {0} FAIL(s), {1} WARN(s) ================" -f $script:Fails, $script:Warns)
Write-Host "Close the LANE B window to stop the lane (this script exits; the window keeps running)."
exit $script:Fails
