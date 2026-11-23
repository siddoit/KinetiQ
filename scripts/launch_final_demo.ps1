param(
    [switch]$NoBrowser
)

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root

$Py = Join-Path $Root "laptop\.venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Py)) { $Py = "python" }

$Adb = Join-Path $env:LOCALAPPDATA "Android\Sdk\platform-tools\adb.exe"
if (-not (Test-Path -LiteralPath $Adb)) { $Adb = "C:\Users\Tharz\AppData\Local\Android\Sdk\platform-tools\adb.exe" }
if (-not (Test-Path -LiteralPath $Adb)) { $Adb = "adb" }

$Token = "poc-demo"
$ReceiverScript = Join-Path $Root "Tharun work\Huawei-kinetiq-band6\backend\kinetiq_receiver.py"
$WatchAdbScript = Join-Path $Root "Tharun work\Huawei-kinetiq-band6\watch_adb_reverse.ps1"
$MirrorScript   = Join-Path $Root "laptop\web\app.py"

function Test-Endpoint([string]$Url) {
    try {
        $r = Invoke-RestMethod -Uri $Url -TimeoutSec 2
        if ($r.ok) { return $true }
    } catch {}
    return $false
}

function Wait-Endpoint([string]$Url, [int]$Seconds = 20) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-Endpoint $Url) { return $true }
        Start-Sleep -Milliseconds 300
    }
    return $false
}

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  KINETIQ - SMART MIRROR / HOME EDITION  (FINAL REVIEW)" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# [1/5] Ollama + qwen3.5:4b warmup
Write-Host "[1/5] Checking Ollama & warming qwen3.5:4b in GPU VRAM..."
$ollamaUp = $false
try {
    Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 2 | Out-Null
    $ollamaUp = $true
} catch {}

if (-not $ollamaUp) {
    try {
        Start-Process -FilePath "ollama" -ArgumentList "serve" -WindowStyle Minimized
        Start-Sleep -Seconds 3
    } catch {
        Write-Host "   WARN - ollama command not found (template fallback ready)." -ForegroundColor Yellow
    }
}

try {
    $body = @{
        model = "qwen3.5:4b"
        prompt = "ok"
        stream = $false
        think = $false
        keep_alive = "60m"
        options = @{ num_predict = 1 }
    } | ConvertTo-Json -Compress
    Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/generate" -Method Post -Body $body -ContentType "application/json" -TimeoutSec 25 | Out-Null
    Write-Host "   OK - qwen3.5:4b warm in VRAM." -ForegroundColor Green
} catch {
    Write-Host "   WARN - Ollama warmup skipped (template fallback ready)." -ForegroundColor Yellow
}

# [2/5] Huawei Band 6 Receiver on :8000
Write-Host ""
Write-Host "[2/5] Starting Huawei Band 6 Receiver v1.2.0 on :8000 ..."
if (Test-Endpoint "http://127.0.0.1:8000/api/health") {
    Write-Host "   OK - Receiver already running on :8000" -ForegroundColor Green
} elseif (Test-Path -LiteralPath $ReceiverScript) {
    $env:KINETIQ_TOKEN = $Token
    Start-Process -FilePath $Py -ArgumentList "`"$ReceiverScript`"" -WorkingDirectory $Root -WindowStyle Minimized
    if (Wait-Endpoint "http://127.0.0.1:8000/api/health" 15) {
        Write-Host "   OK - Receiver v1.2.0 live on http://127.0.0.1:8000" -ForegroundColor Green
    } else {
        Write-Host "   WARN - Receiver did not answer on :8000 yet." -ForegroundColor Yellow
    }
} else {
    Write-Host "   WARN - Receiver script not found at $ReceiverScript" -ForegroundColor Yellow
}

# [3/5] USB ADB Reverse Tunnel + Auto-Healing Watcher
Write-Host ""
Write-Host "[3/5] Setting up USB ADB tunnel + auto-healing watcher..."
try {
    & $Adb reverse tcp:8000 tcp:8000 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "   OK - USB tunnel active (phone posts to http://127.0.0.1:8000)." -ForegroundColor Green
    } else {
        Write-Host "   INFO - No USB ADB device detected right now (Wi-Fi LAN or FreeRTOS lane ready)." -ForegroundColor DarkGray
    }
} catch {
    Write-Host "   INFO - ADB not detected right now (Wi-Fi LAN or FreeRTOS lane ready)." -ForegroundColor DarkGray
}

if (Test-Path -LiteralPath $WatchAdbScript) {
    Start-Process -FilePath "powershell.exe" -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$WatchAdbScript`"") -WindowStyle Minimized
    Write-Host "   OK - Auto-healing ADB watcher running in background." -ForegroundColor Green
}

# [4/5] KINETIQ Mirror Server on :8777
Write-Host ""
Write-Host "[4/5] Starting KINETIQ Mirror Server on :8777 ..."
if (Test-Endpoint "http://127.0.0.1:8777/api/health") {
    Write-Host "   OK - Mirror Server already running on :8777" -ForegroundColor Green
} else {
    Start-Process -FilePath $Py -ArgumentList "`"$MirrorScript`"" -WorkingDirectory $Root -WindowStyle Minimized
    if (Wait-Endpoint "http://127.0.0.1:8777/api/health" 20) {
        Write-Host "   OK - Mirror Server live on http://127.0.0.1:8777" -ForegroundColor Green
    } else {
        Write-Host "   ERROR - Mirror Server failed to answer on :8777!" -ForegroundColor Red
        Write-Host "   Launching visible diagnostic console..." -ForegroundColor Yellow
        Start-Process -FilePath "powershell.exe" -ArgumentList @("-NoProfile", "-NoExit", "-Command", "& '$Py' '$MirrorScript'") -WorkingDirectory $Root
        exit 1
    }
}

# [5/5] Launch Smart Mirror in Frameless App Window
Write-Host ""
Write-Host "[5/5] Opening Smart Mirror (Home Edition)..."
if (-not $NoBrowser -and $env:KINETIQ_NO_BROWSER -ne "1") {
    $browserCandidates = @(
        (Join-Path $env:LOCALAPPDATA "Vivaldi\Application\vivaldi.exe"),
        (Join-Path $env:ProgramFiles "Google\Chrome\Application\chrome.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Google\Chrome\Application\chrome.exe"),
        (Join-Path $env:LOCALAPPDATA "Google\Chrome\Application\chrome.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Microsoft\Edge\Application\msedge.exe"),
        (Join-Path $env:ProgramFiles "Microsoft\Edge\Application\msedge.exe")
    )
    $appBrowser = $browserCandidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
    if ($appBrowser) {
        Start-Process -FilePath $appBrowser -ArgumentList @("--app=http://127.0.0.1:8777", "--start-maximized")
        Write-Host "   OK - Opened frameless mirror window." -ForegroundColor Green
    } else {
        Start-Process "http://127.0.0.1:8777"
        Write-Host "   OK - Opened default browser." -ForegroundColor Green
    }
} else {
    Write-Host "   INFO - Browser launch skipped (NoBrowser test mode)." -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  READY FOR DEMO!" -ForegroundColor Green
Write-Host "  - Home Edition Mirror : http://127.0.0.1:8777/"
Write-Host "  - Classic Dashboard   : http://127.0.0.1:8777/classic"
Write-Host "  - Band 6 Receiver     : http://127.0.0.1:8000/"
Write-Host "  - Voice shortcut      : Press SPACE or tap the Voice Orb"
Write-Host "============================================================" -ForegroundColor Cyan
if (-not $NoBrowser -and $env:KINETIQ_NO_BROWSER -ne "1") {
    Start-Sleep -Seconds 3
}
exit 0
