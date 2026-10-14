# KINETIQ native device node — build script (Windows PowerShell 5.1+).
# Compiles the real FreeRTOS kernel + app with gcc (WinLibs) into
# hardware/native/kinetiq_native.exe. Run from anywhere:
#   powershell -ExecutionPolicy Bypass -File hardware\native\build.ps1

$ErrorActionPreference = "Stop"
$NativeDir = Split-Path -Parent $MyInvocation.MyCommand.Path

function Find-Gcc {
    $cmd = Get-Command gcc.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $pkg = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages"
    $hit = Get-ChildItem -Path $pkg -Recurse -Filter "gcc.exe" -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -match "mingw64\\bin\\gcc\.exe$" } |
        Select-Object -First 1
    if ($hit) { return $hit.FullName }
    $fallbacks = @("C:\mingw64\bin\gcc.exe", "C:\msys64\mingw64\bin\gcc.exe")
    foreach ($p in $fallbacks) {
        if (Test-Path -LiteralPath $p) { return $p }
    }
    return $null
}

$Gcc = Find-Gcc
if (-not $Gcc) {
    Write-Error "gcc.exe not found (PATH, WinGet WinLibs, C:\mingw64). Install BrechtSanders.WinLibs via winget."
    exit 1
}
Write-Host "[kinetiq] gcc: $Gcc"
& $Gcc --version | Select-Object -First 1

$Kernel = Join-Path $NativeDir "freertos-kernel"
foreach ($d in @($NativeDir, "$Kernel\include", "$Kernel\portable\MSVC-MingW")) {
    if (-not (Test-Path -LiteralPath $d)) {
        Write-Error "missing directory: $d"
        exit 1
    }
}

$Sources = @(
    "$Kernel\tasks.c",
    "$Kernel\queue.c",
    "$Kernel\list.c",
    "$Kernel\timers.c",
    "$Kernel\event_groups.c",
    "$Kernel\stream_buffer.c",
    "$Kernel\portable\MemMang\heap_4.c",
    "$Kernel\portable\MSVC-MingW\port.c",
    "$NativeDir\main.c",
    "$NativeDir\kinetiq_tasks.c"
)
foreach ($s in $Sources) {
    if (-not (Test-Path -LiteralPath $s)) {
        Write-Error "missing source: $s"
        exit 1
    }
}

$Exe = Join-Path $NativeDir "kinetiq_native.exe"
$Includes = @("-I", $NativeDir, "-I", "$Kernel\include", "-I", "$Kernel\portable\MSVC-MingW")

$Stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
& $Gcc -std=gnu11 -O2 -Wall -Wextra -Wno-unused-parameter @Includes $Sources -o $Exe -lwinmm -lws2_32 -lm
if ($LASTEXITCODE -ne 0) {
    Write-Error "gcc failed with exit code $LASTEXITCODE"
    exit 1
}
$Stopwatch.Stop()

$Size = (Get-Item -LiteralPath $Exe).Length
Write-Host "[kinetiq] build OK: $Exe ($Size bytes) in $([math]::Round($Stopwatch.Elapsed.TotalSeconds, 1)) s"
