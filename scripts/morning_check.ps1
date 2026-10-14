# KINETIQ morning stage check — single entry point, run from repo root:
#   powershell -ExecutionPolicy Bypass -File scripts\morning_check.ps1
# Exit code = number of FAILs (SKIP never fails). Each section prints PASS/FAIL + evidence.

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location -LiteralPath $Root
$Py = Join-Path $Root "laptop\.venv\Scripts\python.exe"
$script:Fails = 0
$script:Skips = 0

function Record([string]$Name, [string]$Result, [string]$Evidence) {
    Write-Host ""
    Write-Host "[$Result] $Name -- $Evidence"
    if ($Result -eq "FAIL") { $script:Fails++ }
    if ($Result -eq "SKIP") { $script:Skips++ }
}

Write-Host "================ KINETIQ MORNING CHECK ================"

# ---------- 1. Environment ----------
$envFails = @()
$envInfo = @()
if (Test-Path -LiteralPath $Py) { $envInfo += "venv python OK" }
else { $envFails += "venv python MISSING" }

$ollamaOut = ""
try { $ollamaOut = (ollama list 2>&1 | Out-String) } catch { $ollamaOut = "ollama CLI error: $_" }
if ($ollamaOut -match "gemma2:2b") { $envInfo += "ollama gemma2:2b present" }
else { $envFails += "ollama gemma2:2b MISSING" }

$NativeExe = Join-Path $Root "hardware\native\kinetiq_native.exe"
if (-not (Test-Path -LiteralPath $NativeExe)) {
    Write-Host "[env] kinetiq_native.exe missing -> auto-running build.ps1 ..."
    powershell -ExecutionPolicy Bypass -File (Join-Path $Root "hardware\native\build.ps1")
}
if (Test-Path -LiteralPath $NativeExe) {
    $nsz = (Get-Item -LiteralPath $NativeExe).Length
    $envInfo += "kinetiq_native.exe $nsz bytes"
} else { $envFails += "kinetiq_native.exe STILL MISSING after build" }

$head = ""
try { $head = (git rev-parse --short HEAD 2>&1 | Out-String).Trim() } catch { $head = "?" }
$envInfo += "HEAD $head"

$Report = Join-Path $Root "docs\report\report.docx"
$Deck = Join-Path $Root "docs\final review\KINETIQ_V1_final_review.pptx"
foreach ($f in @($Report, $Deck)) {
    if (Test-Path -LiteralPath $f) { $envInfo += ("{0} {1:N0} KB" -f (Split-Path -Leaf $f), ((Get-Item -LiteralPath $f).Length / 1KB)) }
    else { $envFails += ("{0} MISSING" -f $f) }
}
if ($envFails.Count -eq 0) { Record "1. Environment" "PASS" ($envInfo -join "; ") }
else { Record "1. Environment" "FAIL" (($envInfo + $envFails) -join "; ") }

# ---------- 2. Gate 1: dashboard mock 8 s ----------
$g1 = & $Py laptop\receiver\dashboard.py --source mock --duration 8 --headless 2>&1 | Out-String
$g1exit = $LASTEXITCODE
if (($g1exit -eq 0) -and ($g1 -match "dropped=0")) {
    $line = ([regex]::Matches($g1, "received=\d+ dropped=\d+") | Select-Object -Last 1).Value
    Record "2. Gate1 dashboard mock 8s" "PASS" "exit 0; $line"
} else { Record "2. Gate1 dashboard mock 8s" "FAIL" ("exit={0}; tail: {1}" -f $g1exit, ($g1 -split "`r?`n" | Select-Object -Last 2 | Out-String).Trim()) }

# ---------- 3. Gate 2: assistant once ----------
$g2 = & $Py laptop\ai\assistant.py --once --query "How am I doing?" --no-tts 2>&1 | Out-String
$g2exit = $LASTEXITCODE
if (($g2exit -eq 0) -and ($g2 -match "\[grounding\] PASS")) { Record "3. Gate2 assistant once" "PASS" "exit 0; [grounding] PASS" }
else { Record "3. Gate2 assistant once" "FAIL" ("exit={0}; tail: {1}" -f $g2exit, ($g2 -split "`r?`n" | Select-Object -Last 3 | Out-String).Trim()) }

# ---------- 4. Gate 3: ble_client mock 20 ----------
$g3 = & $Py laptop\receiver\ble_client.py --source mock --mock-count 20 2>&1 | Out-String
$g3exit = $LASTEXITCODE
if (($g3exit -eq 0) -and ($g3 -match "schema_rejected=0")) {
    $line = ([regex]::Matches($g3, "packets_routed=\d+.*schema_rejected=\d+") | Select-Object -Last 1).Value
    Record "4. Gate3 ble_client mock 20" "PASS" "exit 0; $line"
} else { Record "4. Gate3 ble_client mock 20" "FAIL" ("exit={0}; tail: {1}" -f $g3exit, ($g3 -split "`r?`n" | Select-Object -Last 2 | Out-String).Trim()) }

# ---------- 5. Gate 4: voice_pipeline --no-tts ----------
$g4 = & $Py laptop\ai\voice_pipeline.py --no-tts 2>&1 | Out-String
$g4exit = $LASTEXITCODE
if ($g4exit -eq 0) {
    $gl = ([regex]::Matches($g4, "\[grounding\] (PASS|FAIL)") | Select-Object -Last 1).Value
    if ([string]::IsNullOrEmpty($gl)) { $gl = ($g4 -split "`r?`n" | Select-Object -Last 1).Trim() }
    Record "5. Gate4 voice_pipeline --no-tts" "PASS" "exit 0; $gl"
} else { Record "5. Gate4 voice_pipeline --no-tts" "FAIL" ("exit={0}; tail: {1}" -f $g4exit, ($g4 -split "`r?`n" | Select-Object -Last 3 | Out-String).Trim()) }

# ---------- 6. Native node pipe (12 s capture -> dashboard stdin replay) ----------
$PipeFile = Join-Path ([System.IO.Path]::GetTempPath()) "kinetiq_native_pipe.txt"
if (Test-Path -LiteralPath $PipeFile) { Remove-Item -LiteralPath $PipeFile -Force }
$NativeProc = Start-Process -FilePath $NativeExe -RedirectStandardOutput $PipeFile -NoNewWindow -PassThru
Start-Sleep -Seconds 12
try { Stop-Process -InputObject $NativeProc -Force -ErrorAction SilentlyContinue } catch {}
Get-Process -Name "kinetiq_native" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1
$g6 = Get-Content -LiteralPath $PipeFile -ErrorAction SilentlyContinue | Where-Object { $_ -match '^\s*\{.*\}\s*$' } | & $Py laptop\receiver\dashboard.py --source stdin --headless 2>&1 | Out-String
$g6exit = $LASTEXITCODE
$mRecv = [regex]::Match($g6, "received=(\d+)")
$mDrop = [regex]::Match($g6, "dropped=(\d+)")
$recv = if ($mRecv.Success) { [int]$mRecv.Groups[1].Value } else { -1 }
$drop = if ($mDrop.Success) { [int]$mDrop.Groups[1].Value } else { -1 }
if (($recv -ge 8) -and ($drop -eq 0)) { Record "6. Native pipe 12s" "PASS" "received=$recv dropped=$drop" }
else { Record "6. Native pipe 12s" "FAIL" ("exit={0}; received={1} dropped={2}" -f $g6exit, $recv, $drop) }

# ---------- 7. Webcam smoke ----------
$g7raw = & $Py laptop\receiver\webcam_motion.py --camera 0 --count 6 2>&1
$g7exit = $LASTEXITCODE
$g7text = ($g7raw | Out-String)
if (($g7exit -ne 0) -and ($g7text -match "(?i)(no camera|camera.*(busy|missing|not found|unavailable)|open.*fail|cannot open|SKIP|dshow|device)")) {
    Record "7. Webcam smoke" "SKIP" ("camera unavailable (exit={0}); {1}" -f $g7exit, (($g7text -split "`r?`n" | Select-Object -Last 1).Trim()))
} else {
    $valid = 0
    foreach ($ln in ($g7text -split "`r?`n")) {
        $t = $ln.Trim()
        if ($t.StartsWith("{") -and ($t -match '"activity"') -and ($t -match '"heart_rate"')) { $valid++ }
    }
    if ($valid -ge 6) { Record "7. Webcam smoke" "PASS" "$valid/6 schema-valid lines" }
    elseif (($g7exit -ne 0) -or ($valid -eq 0)) {
        Record "7. Webcam smoke" "SKIP" ("no camera (exit={0}, valid={1}); re-run at venue" -f $g7exit, $valid)
    } else { Record "7. Webcam smoke" "FAIL" ("only {0}/6 valid lines, exit={1}" -f $valid, $g7exit) }
}

# ---------- 8. Warmup reminder ----------
$g8 = & $Py laptop\ai\assistant.py --once --query "Summarize my activity" --no-tts 2>&1 | Out-String
if ($g8 -match "\[grounding\] PASS") { Record "8. Warmup (Ollama hot)" "PASS" "[grounding] PASS; model warm" }
else { Record "8. Warmup (Ollama hot)" "FAIL" ("tail: {0}" -f ($g8 -split "`r?`n" | Select-Object -Last 2 | Out-String).Trim()) }

Write-Host ""
Write-Host "Stage-ready checklist: HDMI adapter packed? presenter view on? mic unmuted?"
Write-Host "  video cable + clicker? water? phone SILENT? fallback: soft-AP 192.168.4.1."
Write-Host ""
Write-Host ("================ RESULT: {0} FAIL(s), {1} SKIP(s) =================" -f $script:Fails, $script:Skips)
exit $script:Fails
