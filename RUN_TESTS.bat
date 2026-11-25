@echo off
REM Double-click to run every Ridgeline check before you push to GitHub.
cd /d "%~dp0"
python -m pip install -q -r requirements.txt -r requirements-dev.txt
python check_deploy.py
echo.
pause
