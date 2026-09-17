@echo off
title AI Job Search Agent - Telegram Bot Listener
cd /d "%~dp0"
echo ========================================================
echo  AI Job Search Agent - Telegram Callback Bot (v4.2)
echo ========================================================
echo Bot is listening for "Like" and "Reject" buttons in Telegram...
echo Press Ctrl+C to stop.
echo.

:loop
python telegram_bot.py
echo.
echo [WARNING] Bot process stopped. Restarting in 5 seconds...
timeout /t 5 /nobreak >nul
goto loop
