@echo off
REM ============================================================================
REM Telegram Daily News Bot - Live 24/7 Watcher (1-min Poller)
REM Keeps running continuously in the background, checking every 60 seconds.
REM ============================================================================

set SCRIPT_DIR=%~dp0
cd /d "%SCRIPT_DIR%"

if not exist logs mkdir logs

echo Starting Telegram News Watcher (Polling every 60 seconds)...
echo Window can be minimized. Press Ctrl+C in this console to stop.

"%SCRIPT_DIR%venv\Scripts\python.exe" main.py watch
