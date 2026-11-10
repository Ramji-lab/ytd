@echo off
title ClipFetch - YouTube Downloader
cd /d "%~dp0"
echo ==========================================
echo        ClipFetch YouTube Downloader
echo ==========================================
where py >nul 2>nul
if errorlevel 1 (
  echo Python was not found. Install Python 3.10 or newer from https://www.python.org/downloads/
  echo During installation, tick "Add Python to PATH".
  pause
  exit /b 1
)
if not exist .venv (
  echo Creating Python environment...
  py -3 -m venv .venv
)
call .venv\Scripts\activate
echo Installing or updating required packages...
python -m pip install --upgrade pip
pip install -r requirements.txt
echo.
echo Starting ClipFetch...
echo Open http://127.0.0.1:5000 in your browser.
echo Keep this window open while downloading.
start "" http://127.0.0.1:5000
python app.py
pause
