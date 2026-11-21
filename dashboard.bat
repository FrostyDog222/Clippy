@echo off
rem Starts the dashboard hidden in the background and opens it in your browser. Close it with the Quit button on the page.
cd /d "%~dp0"
start "" .venv\Scripts\pythonw.exe dashboard.py
timeout /t 2 /nobreak >nul
start "" http://127.0.0.1:8765
