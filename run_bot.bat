@echo off
REM ============================================================================
REM Telegram Daily News Bot - Single Poll Runner
REM Checks feeds once, publishes any new articles, and exits cleanly.
REM Suitable for Windows Task Scheduler.
REM ============================================================================

set SCRIPT_DIR=%~dp0
cd /d "%SCRIPT_DIR%"

REM Create logs directory if missing
if not exist logs mkdir logs

REM Run bot using venv Python and append output to logs/bot.log
"%SCRIPT_DIR%venv\Scripts\python.exe" main.py run >> logs\bot.log 2>&1
