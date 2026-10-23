# KINETIQ LANE A (real Huawei Band 6 -> mirror) — single entry point, run from repo root:
#   powershell -ExecutionPolicy Bypass -File scripts\demo_band_lane.ps1
# Optional (rehearsal against a copy of the teammate repo, e.g. to avoid writing to their tree):
#   powershell -ExecutionPolicy Bypass -File scripts\demo_band_lane.ps1 -BandRepo "$env:TEMP\Huawei-kinetiq-band6"
# Exit code = number of FAILs (WARN never fails). Every check prints PASS/FAIL/WARN + evidence.
#
# Chain started (two of the three windows outlive this script):
#   [window 1] python backend\kinetiq_receiver.py            (port 8000, own window, log on screen)
#   [window 2] python kinetiq-integration\band_bridge.py --kinetiq-repo <this repo>
#                | python laptop\web\app.py --stdin --lane band
#
# Read-only with respect to `Tharun work\`: nothing here edits that repo. Note that the
# receiver *itself*, by design, appends its own CSV rows under backend\data\ while it runs.
#
# Documented receiver invocation (Tharun work\Huawei-kinetiq-band6\README.md):
#   python backend\kinetiq_receiver.py     # token via KINETIQ_TOKEN or backend/config.json
# It takes NO command-line flags; a token is mandatory (it refuses to start without one).
#
# Token: the receiver's expected token is whatever the phone app POSTs as `X-KinetiQ-Token`
# (or `Authorization: Bearer ...`). The app ships with the default `poc-demo`
# (kinetiq-streamer\app\src\main\java\com\kinetiq\streamer\Prefs.kt: DEFAULT_TOKEN), which is
# also what the teammate's own start_demo.bat exports. That value is the -Token default here;
# the receiver is started with KINETIQ_TOKEN set inside its own window only.

param(
    # Teammate repo that holds the receiver + bridge. Override only to rehearse against a copy:
    #   powershell -File scripts\demo_band_lane.ps1 -BandRepo "$env:TEMP\Huawei-kinetiq-band6"
    [string]$BandRepo = "",

    # Shared token for POST /api/wearable. Defaults to the phone app's built-in value
    # (`poc-demo`, kinetiq-streamer Prefs.kt DEFAULT_TOKEN / start_demo.bat TOKEN).
    # Pass another value only if the app's token was changed in its settings screen.
    # An explicit $env:KINETIQ_TOKEN still wins over this parameter.
    [string]$Token = "poc-demo"
)

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root

if ([string]::IsNullOrWhiteSpace($BandRepo)) { $BandRepo = Join-Path $Root "Tharun work\Huawei-kinetiq-band6" }
$BandRepoRequested = $BandRepo
if (Test-Path -LiteralPath $BandRepo) { $BandRepo = (Resolve-Path -LiteralPath $BandRepo).Path }

$Receiver = Join-Path $BandRepo "backend\kinetiq_receiver.py"
$Bridge   = Join-Path $BandRepo "kinetiq-integration\band_bridge.py"
$App      = Join-Path $Root "laptop\web\app.py"
$VenvPy   = Join-Path $Root "laptop\.venv\Scripts\python.exe"
$Port     = 8000
$ReceiverUrl = "http://127.0.0.1:$Port/api/wearable/latest"

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
    Write-Host "LANE A was NOT started. Nothing is running because of this script."
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

function Test-OursReceiver([string]$Url) {
    try {
        $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
        return (($r.StatusCode -eq 200) -and ($r.Content -match '"real"'))
    } catch { return $false }
}

# One visible console window running a cmd pipeline (same pattern as KINETIQ_DEMO.bat).
# cmd is used instead of `powershell -NoExit -Command`: raw bytes on the pipe (no
# re-encoding), the bridge's stderr stays on screen, and the window title sticks.
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

Write-Host "================ KINETIQ LANE A (REAL BAND) ================"

# ---------- 1. Repo layout (hard) ----------
$missing = @()
if (-not (Test-Path -LiteralPath $BandRepo)) { $missing += ("band repo " + $BandRepoRequested) }
if (-not (Test-Path -LiteralPath $Receiver)) { $missing += $Receiver }
if (-not (Test-Path -LiteralPath $Bridge))   { $missing += $Bridge }
if (-not (Test-Path -LiteralPath $App))      { $missing += $App }
if ($missing.Count -gt 0) {
    Abort "1. Repo layout" ("missing: " + ($missing -join "; ")) `
        "expected the teammate repo at 'Tharun work\Huawei-kinetiq-band6' (backend\kinetiq_receiver.py + kinetiq-integration\band_bridge.py) and laptop\web\app.py in this repo. Check the folder name/typo, then re-run. (-BandRepo overrides the repo path.)"
}
Record "1. Repo layout" "PASS" ("receiver + bridge at $BandRepo; app.py at laptop\web\app.py")

# ---------- 2. Python (hard) ----------
$Py = $null
$pyNote = ""
if (Test-Path -LiteralPath $VenvPy) { $Py = $VenvPy; $pyNote = "project venv laptop\.venv" }
else {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { $Py = $cmd.Source; $pyNote = "python on PATH (laptop\.venv absent)" }
}
if (-not $Py) {
    Abort "2. Python" "no laptop\.venv\Scripts\python.exe and no 'python' on PATH" `
        "create the venv (py -3 -m venv laptop\.venv; laptop\.venv\Scripts\python -m pip install -r laptop\requirements.txt) or put python on PATH."
}
$pyVer = (& $Py --version 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0) {
    Abort "2. Python" ("'$Py' did not run: $pyVer") "repair/recreate laptop\.venv, then re-run."
}
Record "2. Python" "PASS" "$pyVer ($pyNote)"

# ---------- 3. Port 8000 ----------
$Listening = Test-PortListening $Port
$Ours = $false
if ($Listening) { $Ours = Test-OursReceiver $ReceiverUrl }
if ($Listening -and $Ours) {
    Record "3. Port $Port" "PASS" "already serving a KINETIQ receiver -> reusing it (not restarted)"
} elseif ($Listening) {
    Abort "3. Port $Port" "port $Port is LISTENING but /api/wearable/latest is not our receiver (no JSON answer)" `
        "find and stop the other owner: 'netstat -ano | findstr :$Port' then 'Stop-Process -Id <PID> -Force' (or close that app), then re-run."
} else {
    Record "3. Port $Port" "PASS" "free -> receiver will be started here"
}

# ---------- 4. adb (warnings only: the manual-refresh ritual still works) ----------
$adbFix = "fix: 'adb kill-server; adb start-server', plug the USB cable, and tap ALLOW on the phone's USB-debugging prompt."
$adb = Get-Command adb -ErrorAction SilentlyContinue
if (-not $adb) {
    Record "4. adb" "WARN" "adb.exe not found on PATH -- LANE A can still run if the receiver already has data. $adbFix"
} else {
    $adbOut = (& adb devices 2>&1 | Out-String)
    $states = @()
    foreach ($ln in ($adbOut -split "`r?`n")) {
        $t = $ln.Trim()
        if ($t -eq "" -or $t -match '^List of devices' -or $t -match '^\*') { continue }
        $parts = $t -split '\s+'
        if ($parts.Count -ge 2) { $states += $parts[1] }
    }
    $dev = @($states | Where-Object { $_ -eq "device" })
    $unauth = @($states | Where-Object { $_ -eq "unauthorized" })
    if ($dev.Count -gt 0) {
        Record "4. adb" "PASS" ("device attached: " + ($dev -join ", "))
        $rev = (& adb reverse tcp:$Port tcp:$Port 2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -eq 0) { Record "4b. adb reverse" "PASS" "tcp:$Port -> tcp:$Port on the phone (USB path ready)" }
        else { Record "4b. adb reverse" "WARN" "adb reverse failed ($rev). $adbFix" }
    } elseif ($unauth.Count -gt 0) {
        Record "4. adb" "WARN" "phone is UNAUTHORIZED -- accept the USB debugging prompt on the phone. $adbFix"
    } else {
        Record "4. adb" "WARN" "no phone attached (adb devices empty) -- Wi-Fi/relay-only run. $adbFix"
    }
}

# ---------- 5. Receiver token (hard only if we must start the receiver) ----------
# Precedence matches the receiver's own: an explicitly exported KINETIQ_TOKEN wins, then an
# explicit -Token, then backend\config.json's auth_token, then the built-in app default.
$TokenSource = ""
$EffectiveToken = ""
if (-not [string]::IsNullOrWhiteSpace($env:KINETIQ_TOKEN)) {
    $EffectiveToken = $env:KINETIQ_TOKEN.Trim()
    $TokenSource = "KINETIQ_TOKEN env var (overrides -Token)"
} elseif (-not [string]::IsNullOrWhiteSpace($Token)) {
    $EffectiveToken = $Token.Trim()
    if ($PSBoundParameters.ContainsKey('Token')) { $TokenSource = "-Token parameter" }
    else { $TokenSource = "built-in default 'poc-demo' (phone app Prefs.kt DEFAULT_TOKEN)" }
} else {
    $cfg = Join-Path $BandRepo "backend\config.json"
    if (Test-Path -LiteralPath $cfg) {
        try {
            $j = Get-Content -LiteralPath $cfg -Raw | ConvertFrom-Json
            if ($j.auth_token) { $EffectiveToken = [string]$j.auth_token; $TokenSource = "backend\config.json" }
        } catch { }
    }
}
# The token is written into a .cmd launcher, so refuse characters cmd would reinterpret.
$TokenUnsafe = $EffectiveToken -match '[\r\n"&|<>^%]'
if ($EffectiveToken -ne "" -and $TokenUnsafe) {
    Abort "5. Receiver token" ("token from " + $TokenSource + " contains a cmd metacharacter (" + '"' + " & | < > ^ %) or a newline") `
        "use a token the receiver window can carry safely: pass a plain value with -Token, or export KINETIQ_TOKEN."
}
if ($Ours) {
    Record "5. Receiver token" "PASS" "not needed -- receiver already running and answering (its token is whatever it booted with)"
} elseif ($EffectiveToken -ne "") {
    Record "5. Receiver token" "PASS" "token from $TokenSource"
} else {
    Abort "5. Receiver token" "no KINETIQ_TOKEN, no -Token, and no backend\config.json with auth_token -> the receiver exits immediately (it refuses to start unauthenticated)" `
        ("set -Token '<token>' or export KINETIQ_TOKEN (default in use: 'poc-demo', the phone app's built-in token), or copy '{0}\backend\config.example.json' to that folder's backend\config.json and set auth_token." -f $BandRepoRequested)
}

# ---------- 6. Start the receiver (own window, log on screen) ----------
if (-not $Ours) {
    # The token is exported inside the receiver's own launcher only, so no config.json is
    # written into the teammate's repo and the bridge window never sees it.
    $tmpBase = Join-Path ([System.IO.Path]::GetTempPath()) "opencode"
    if (-not (Test-Path -LiteralPath $tmpBase)) { New-Item -ItemType Directory -Path $tmpBase -Force | Out-Null }
    $recvBat = Join-Path $tmpBase "kinetiq_receiver.cmd"
    $recvLines = @(
        "@echo off",
        "title KINETIQ LANE A - receiver",
        ("cd /d ""{0}""" -f $BandRepo),
        ("set ""KINETIQ_TOKEN={0}""" -f $EffectiveToken),
        ("""{0}"" backend\kinetiq_receiver.py" -f $Py)
    )
    Set-Content -LiteralPath $recvBat -Value $recvLines -Encoding ASCII
    Write-Host ""
    Write-Host ('[run] cd "{0}"; set KINETIQ_TOKEN=*** ({1}); "{2}" backend\kinetiq_receiver.py' -f $BandRepo, $TokenSource, $Py)
    Start-Process -FilePath "cmd.exe" -ArgumentList "/c", ('"{0}"' -f $recvBat) -WorkingDirectory $BandRepo | Out-Null

    $up = $false
    for ($i = 0; $i -lt 24; $i++) {
        Start-Sleep -Milliseconds 500
        if (Test-PortListening $Port) { $up = $true; break }
    }
    if (-not $up) {
        Abort "6. Receiver start" "port $Port never came up within 12s" `
            ("read the receiver window for the reason (most often a missing/invalid token). If the window vanished, run it by hand: '{0} backend\kinetiq_receiver.py' inside '{1}'." -f $Py, $BandRepoRequested)
    }
    $answering = $false
    for ($i = 0; $i -lt 20; $i++) {
        if (Test-OursReceiver $ReceiverUrl) { $answering = $true; break }
        Start-Sleep -Milliseconds 500
    }
    if (-not $answering) {
        Abort "6. Receiver start" "listening on $Port but GET /api/wearable/latest did not answer with a KINETIQ snapshot" `
            "check the receiver window; a foreign listener may have grabbed the port after the check. 'netstat -ano | findstr :$Port'."
    }
    Record "6. Receiver start" "PASS" "listening on $Port, /api/wearable/latest answering"
}

# ---------- 7. Bridge -> mirror (own window; bridge stderr stays visible) ----------
# --kinetiq-repo is explicit: the bridge default points at external\KinetiQ, which does not exist here.
Write-Host ""
Write-Host ('[run] "{0}" "{1}" --kinetiq-repo "{2}" --receiver http://127.0.0.1:{3} | "{0}" "{4}" --stdin --lane band' -f $Py, $Bridge, $Root, $Port, $App)
$pipeline = '"{0}" "{1}" --kinetiq-repo "{2}" --receiver "http://127.0.0.1:{3}" | "{0}" "{4}" --stdin --lane band' -f $Py, $Bridge, $Root, $Port, $App
$before = @(8777, 8778 | Where-Object { Test-PortListening $_ })
$bat = Start-LaneWindow "KINETIQ LANE A - bridge to mirror" $pipeline "lane_a"
Write-Host ('[lane window] {0}' -f $bat)

$appPort = $null
for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Milliseconds 500
    if ($before -notcontains 8777 -and (Test-PortListening 8777)) { $appPort = 8777; break }
    if ($before -notcontains 8778 -and (Test-PortListening 8778)) { $appPort = 8778; break }
}
if (-not $appPort) {
    foreach ($p in @(8777, 8778)) { if ($before -contains $p) { $appPort = $p; break } }
}
if ($appPort -and ($before -notcontains $appPort)) {
    Record "7. Mirror" "PASS" "http://127.0.0.1:$appPort/  (lane=band, stdin source)"
    Start-Process ("http://127.0.0.1:{0}/" -f $appPort) | Out-Null
} elseif ($appPort) {
    Record "7. Mirror" "WARN" "port $appPort was already serving a mirror before this run -- reusing that page; close the old window to bind a fresh one"
    Start-Process ("http://127.0.0.1:{0}/" -f $appPort) | Out-Null
} else {
    Record "7. Mirror" "WARN" "mirror did not bind 8777/8778 within 20s -- read the LANE A window for the error"
}

# ---------- 8. Feed-rate probe (informational, never fails the run) ----------
# With the phone untouched, 0 arrivals is the CORRECT reading: the bridge refuses to emit
# until the receiver reports a live genuine reading, and says so on its stderr.
if ($appPort) {
    try {
        $uri = "http://127.0.0.1:$appPort/api/telemetry"
        $a = (Invoke-WebRequest -Uri $uri -UseBasicParsing -TimeoutSec 5).Content | ConvertFrom-Json
        Start-Sleep -Seconds 6
        $b = (Invoke-WebRequest -Uri $uri -UseBasicParsing -TimeoutSec 5).Content | ConvertFrom-Json
        $delta = [int]$b.freshness.arrivals - [int]$a.freshness.arrivals
        if ($delta -ge 1) {
            Record "8. Feed rate" "PASS" ("{0} packet(s) in 6s; state={1} link={2} hr={3} spo2={4} steps={5}" -f $delta, $b.freshness.state, $b.link.state, $b.latest.heart_rate, $b.latest.spo2, $b.latest.steps)
        } else {
            Record "8. Feed rate" "WARN" ("0 packets in 6s (state={0}) -- expected until the phone refreshes Huawei Health; the bridge's reason is on stderr in the LANE A window" -f $b.freshness.state)
        }
    } catch {
        Record "8. Feed rate" "WARN" ("could not read the mirror API: {0}" -f $_.Exception.Message)
    }
}

# ---------- Operator card ----------
Write-Host ""
Write-Host "================ LANE A OPERATOR CARD ================"
Write-Host "  1. Open Huawei Health on the phone."
Write-Host "  2. Pull to refresh."
Write-Host "  3. Watch LINK go green."
Write-Host "  4. Expect UNCHANGED between refreshes -- that is correct."
Write-Host ""
Write-Host "  Why nothing is emitted: the bridge prints its reason on stderr in the LANE A window"
Write-Host "  (e.g. a missing SpO2 value, or a stale/offline reading). Nothing is ever faked."
Write-Host ""
Write-Host "  KNOWN LIMITATION (not fixed on purpose -- the receiver is not ours to edit):"
Write-Host "  the receiver binds 0.0.0.0:$Port. POST /api/wearable is token-gated; the GETs are NOT."
Write-Host "  Keep the demo on a trusted local network (or USB-only via adb reverse)."
Write-Host ""
Write-Host "  NOTE: the receiver appends its own CSV rows under the repo's backend\data\:"
Write-Host ("        {0}" -f (Join-Path $BandRepoRequested "backend\data"))
Write-Host ""
Write-Host ("================ LANE A UP: {0} FAIL(s), {1} WARN(s) ================" -f $script:Fails, $script:Warns)
Write-Host "Close the two windows to stop LANE A (this script exits; the demo windows keep running)."
exit $script:Fails
