@echo off
title KINETIQ Stage Launcher
cd /d "%~dp0"

rem ---- direct mode: KINETIQ_DEMO.bat dash|ai|hero|voice|webcam|all|check (no menu, for testing) ----
if not "%~1"=="" goto direct

:menu
cls
echo ================================================
echo    KINETIQ STAGE LAUNCHER   (repo: %~dp0)
echo ================================================
echo    [1] Mirror dashboard  (live wearable feed)
echo    [2] Ask the grounded AI  ("How am I doing?")
echo    [3] HERO: FreeRTOS node -^> Mirror
echo        (then press 1/2/3 in THAT window: run!)
echo    [4] Voice pipeline  (STT -^> AI -^> TTS)
echo    [5] Webcam mirror  (the mirror sees you)
echo    [6] WEB dashboard (browser)
echo    [A] Open full stage set  (1 + 2 + 3)
echo    [M] Run morning check  (~4 min)
echo    [Q] Quit launcher  (demo windows stay open)
echo ================================================
choice /c 123456AMQ /n /m "Choose: "
if errorlevel 9 exit /b 0
if errorlevel 8 (call :spawn_check & goto menu)
if errorlevel 7 (call :spawn_all    & goto menu)
if errorlevel 6 (call :spawn_web   & goto menu)
if errorlevel 5 (call :spawn_webcam & goto menu)
if errorlevel 4 (call :spawn_voice  & goto menu)
if errorlevel 3 (call :spawn_hero   & goto menu)
if errorlevel 2 (call :spawn_ai     & goto menu)
if errorlevel 1 (call :spawn_dash   & goto menu)
goto menu

rem ==================== direct dispatch ====================
:direct
call :spawn_%1
exit /b %errorlevel%

rem ==================== spawn subroutines ====================
:spawn_dash
start "KINETIQ 1: Mirror Dashboard" powershell -NoExit -Command "& '%~dp0laptop\.venv\Scripts\python.exe' '%~dp0laptop\receiver\dashboard.py' --source mock"
exit /b 0

:spawn_ai
rem Ensure Ollama server is active for local LLM inference
curl.exe -s http://127.0.0.1:11434/api/tags >nul 2>&1 || (
    start "Ollama Server" /min ollama serve
    timeout /t 2 /nobreak >nul
)
start "KINETIQ 2: Grounded AI" powershell -NoExit -Command "& '%~dp0laptop\.venv\Scripts\python.exe' '%~dp0laptop\ai\assistant.py' --query 'How am I doing?' --once --no-tts"
exit /b 0

:spawn_hero
start "KINETIQ 3: FreeRTOS node -> Mirror  (press 3 = RUN)" powershell -NoExit -Command "& '%~dp0hardware\native\kinetiq_native.exe' --run-seconds 3600 | & '%~dp0laptop\.venv\Scripts\python.exe' '%~dp0laptop\receiver\dashboard.py' --source stdin"
exit /b 0

:spawn_voice
curl.exe -s http://127.0.0.1:11434/api/tags >nul 2>&1 || (
    start "Ollama Server" /min ollama serve
    timeout /t 2 /nobreak >nul
)
start "KINETIQ 4: Voice pipeline" powershell -NoExit -Command "& '%~dp0laptop\.venv\Scripts\python.exe' '%~dp0laptop\ai\voice_pipeline.py' --no-tts"
exit /b 0

:spawn_webcam
start "KINETIQ 5: Webcam mirror" powershell -NoExit -Command "& '%~dp0laptop\.venv\Scripts\python.exe' '%~dp0laptop\receiver\webcam_motion.py' --camera 0 | & '%~dp0laptop\.venv\Scripts\python.exe' '%~dp0laptop\receiver\dashboard.py' --source stdin"
exit /b 0

:spawn_web
curl.exe -s http://127.0.0.1:11434/api/tags >nul 2>&1 || (
    start "Ollama Server" /min ollama serve
    timeout /t 2 /nobreak >nul
)
start "KINETIQ Receiver (Band :8000)" /min powershell -NoExit -Command "$env:KINETIQ_TOKEN='poc-demo'; & '%~dp0laptop\.venv\Scripts\python.exe' '%~dp0Tharun work\Huawei-kinetiq-band6\backend\kinetiq_receiver.py'"
start "KINETIQ WEB mirror" powershell -NoExit -Command "& '%~dp0laptop\.venv\Scripts\python.exe' '%~dp0laptop\web\app.py'"
timeout /t 3 /nobreak >nul
start "" "http://127.0.0.1:8777"
exit /b 0

:spawn_all
call :spawn_dash
timeout /t 2 /nobreak >nul
call :spawn_ai
timeout /t 2 /nobreak >nul
call :spawn_hero
exit /b 0

:spawn_check
powershell -ExecutionPolicy Bypass -File "%~dp0scripts\morning_check.ps1"
echo.
pause
exit /b 0
