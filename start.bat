@echo off
cd /d "%~dp0"
py -m pip install -r requirements.txt
if errorlevel 1 goto end
py bot.py
:end
pause
