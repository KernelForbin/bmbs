@echo off
cd /d "%~dp0"
".venv\Scripts\pythonw.exe" -u bot.py >> bot.log 2>&1
