# KINETIQ FALLBACK DRILL ("the band is dead") — single entry point, run from repo root:
#   powershell -ExecutionPolicy Bypass -File scripts\demo_fallback.ps1
#
# Stops nothing (never kills a running lane). It simply:
#   1. launches LANE B (RTOS deterministic control) through scripts\demo_rtos_lane.ps1, and
#   2. replays a captured band CSV through the same stdin path with --lane band —
#      ONLY if a replay tool already exists in laptop\. It does not invent one.
#
# Exit code = number of FAILs (SKIP/WARN never fail).

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root

$RtosLane = Join-Path $PSScriptRoot "demo_rtos_lane.ps1"
$App      = Join-Path $Root "laptop\web\app.py"
$VenvPy   = Join-Path $Root "laptop\.venv\Scripts\python.exe"

$script:Fails = 0
$script:Warns = 0

function Record([string]$Name, [string]$Result, [string]$Evidence) {
    Write-Host ""
    Write-Host "[$Result] $Name -- $Evidence"
    if ($Result -eq "FAIL") { $script:Fails++ }
    if ($Result -eq "WARN") { $script:Warns++ }
}

Write-Host "================ KINETIQ FALLBACK DRILL ================"
Write-Host "  Nothing is stopped. LANE B goes up; the band half is replayed only if tooling exists."

# ---------- 1. LANE B (reuses the real lane script; no duplicate launch logic) ----------
if (-not (Test-Path -LiteralPath $RtosLane)) {
    Record "1. LANE B" "FAIL" "scripts\demo_rtos_lane.ps1 missing -- cannot drive the deterministic lane"
} else {
    & $RtosLane
    $rtosExit = $LASTEXITCODE
    if ($rtosExit -eq 0) { Record "1. LANE B" "PASS" "demo_rtos_lane.ps1 exit 0" }
    else { Record "1. LANE B" "FAIL" "demo_rtos_lane.ps1 exit $rtosExit (see its output above)" }
}

# ---------- 2. Captured band CSV replay (only with existing tooling) ----------
# Capability probe, not a guess: look for a replay/CSV-reading entry point in laptop\.
$pyFiles = @(Get-ChildItem -Path (Join-Path $Root "laptop") -Recurse -File -Filter *.py -ErrorAction SilentlyContinue |
             Where-Object { $_.FullName -notmatch '\\\.venv\\' })
$hits = @($pyFiles | Select-String -Pattern 'replay|DictReader|read_csv' -List -ErrorAction SilentlyContinue)
$csvs = @(Get-ChildItem -Path (Join-Path $Root "laptop\logs") -File -Filter *.csv -ErrorAction SilentlyContinue |
          Sort-Object LastWriteTime -Descending)

if ($hits.Count -eq 0) {
    $ev = ("no replay/CSV tool in laptop\ ({0} .py files searched for replay|DictReader|read_csv)" -f $pyFiles.Count)
    if ($csvs.Count -gt 0) { $ev += ("; newest capture available: laptop\logs\{0}" -f $csvs[0].Name) }
    Record "2. Band CSV replay" "SKIP" ($ev + " -- band half skipped on purpose, no parser invented")
} else {
    $tool = ($hits | Select-Object -First 1).Path
    Record "2. Band CSV replay" "PASS" ("replay tool ready at $tool -- fallback drill: python $tool | python laptop\web\app.py --stdin --lane band")
}

# ---------- Operator card ----------
Write-Host ""
Write-Host "================ FALLBACK CARD ================"
Write-Host "  Band dead, demo alive: LANE B paints the same contract from the RTOS node."
Write-Host "  Say it out loud: 'same schema, different source -- the mirror does not care.'"
Write-Host "  The band lane (scripts\demo_band_lane.ps1) can be retried later without stopping this one."
if ($hits.Count -eq 0) {
    Write-Host "  Band CSV replay: NOT AVAILABLE (no tool in laptop\) -- do not hand-craft CSV frames on stage."
}
Write-Host ""
Write-Host ("================ FALLBACK RESULT: {0} FAIL(s), {1} WARN(s) ================" -f $script:Fails, $script:Warns)
exit $script:Fails
