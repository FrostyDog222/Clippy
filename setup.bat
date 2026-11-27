@echo off
rem First-time setup for Clippy on a new Windows PC. Safe to run again.
rem Installs only into this folder (a .venv), plus ffmpeg via winget if you say yes. No admin rights, no auto-start, no background jobs.
setlocal
cd /d "%~dp0"
title Clippy - first time setup
echo.
echo ===== Clippy: first-time setup =====
echo This folder: %CD%
echo.

rem ---- 1. Python 3.10+ ----
where python >nul 2>&1
if errorlevel 1 goto nopython
python -c "import sys;sys.exit(0 if sys.version_info>=(3,10) else 1)"
if errorlevel 1 goto nopython
for /f "delims=" %%v in ('python --version') do echo [OK] %%v
goto python_ok
:nopython
echo [!!] Python 3.10 or newer was not found.
choice /m "Install Python 3.12 now with winget"
if errorlevel 2 (
  echo Install Python from https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^) and run setup.bat again.
  pause & exit /b 1
)
winget install -e --id Python.Python.3.12
echo.
echo Python installed. Close this window, open a NEW one and run setup.bat again.
pause & exit /b 0
:python_ok

rem ---- 2. Virtual environment + packages ----
if not exist ".venv\Scripts\python.exe" (
  echo Creating the virtual environment ^(.venv^)...
  python -m venv .venv || (echo [!!] Could not create .venv & pause & exit /b 1)
)
echo Installing packages ^(a few minutes the first time^)...
.venv\Scripts\python.exe -m pip install -q --upgrade pip
if exist requirements.txt .venv\Scripts\python.exe -m pip install -q -r requirements.txt || (echo [!!] requirements.txt failed & pause & exit /b 1)
.venv\Scripts\python.exe -m pip install -q -r requirements-extra.txt || (echo [!!] requirements-extra.txt failed & pause & exit /b 1)
echo [OK] Python packages installed

rem ---- 3. ffmpeg ----
where ffmpeg >nul 2>&1
if errorlevel 1 (
  echo [!!] ffmpeg not found ^(needed to cut videos^).
  choice /m "Install ffmpeg now with winget"
  if not errorlevel 2 (
    winget install -e --id Gyan.FFmpeg
    echo ffmpeg installed. If the check below still fails, close this window and run setup.bat again.
  )
)
where ffmpeg >nul 2>&1 && echo [OK] ffmpeg found || echo [!!] ffmpeg still missing: install it from https://ffmpeg.org and rerun

rem ---- 3b. JavaScript runtime for yt-dlp (YouTube needs one to solve its challenges) ----
set JSRT=
where deno >nul 2>&1 && set JSRT=1
where node >nul 2>&1 && set JSRT=1
if defined JSRT goto jsrt_ok
echo [!!] No JavaScript runtime found. yt-dlp needs Deno or Node.js to download from YouTube.
choice /m "Install Deno now with winget"
if not errorlevel 2 winget install -e --id DenoLand.Deno
echo If downloads fail later, close this window and run setup.bat again after installing Deno or Node.js.
goto jsrt_done
:jsrt_ok
echo [OK] JavaScript runtime found
:jsrt_done

rem ---- 4. Browser (Google Chrome, or Microsoft Edge which every Windows 10 / 11 PC already has) ----
set CHROME=
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set CHROME=Google Chrome
if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set CHROME=Google Chrome
if not defined CHROME if exist "%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe" set CHROME=Google Chrome
if not defined CHROME if exist "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" set CHROME=Microsoft Edge
if not defined CHROME if exist "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe" set CHROME=Microsoft Edge
if defined CHROME (echo [OK] %CHROME% found) else echo [!!] No browser found: install Google Chrome from https://www.google.com/chrome/ (Microsoft Edge also works)

rem ---- 5. Claude CLI (only for the "Claude in Chrome" upload method) ----
where claude >nul 2>&1 && echo [OK] Claude CLI found || echo [--] Claude CLI not found. Optional: only for Claude-written titles or the "Claude in Chrome" upload method. Without it the tool uses real-quote titles and the Chrome script.

rem ---- 6. Config ----
.venv\Scripts\python.exe scripts\init_config.py
.venv\Scripts\python.exe tick.py --selftest

rem ---- 7. Models (one-time downloads; the only time this tool contacts anything besides YouTube / TikTok / Twitch / your AI API) ----
echo.
echo Clippy blocks every network connection except YouTube, TikTok, Twitch, Google/Gemini APIs and the AI address you set.
echo The two models below are the only exceptions: one download each, right now, then never again. No data of yours is sent.
choice /m "Download the speech-recognition model now (about 500 MB, from huggingface.co; required)"
if not errorlevel 2 (
  set CLIPPY_ALLOW_HF=1
  .venv\Scripts\python.exe -c "from faster_whisper import WhisperModel; WhisperModel('small'); print('[OK] speech model ready')"
  set CLIPPY_ALLOW_HF=
)
choice /m "Download the face-detection model now (about 0.2 MB, from github.com/opencv, checked against a fixed SHA-256; required for the webcam layout)"
if not errorlevel 2 (
  set CLIPPY_ALLOW_GITHUB=1
  .venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'scripts'); import facecam; facecam.model_path(); print('[OK] face model ready')"
  set CLIPPY_ALLOW_GITHUB=
)
rem the network guard: Python runs scripts\sitecustomize_clippy.py at start-up in this venv
copy /y scripts\sitecustomize_clippy.py .venv\Lib\site-packages\sitecustomize.py >nul && echo [OK] network guard installed

rem ---- 8. Sign in once (your own login, never typed by this tool) ----
echo.
echo To post without Claude, sign in to YouTube and TikTok once in the dedicated Chrome profile.
choice /m "Open that Chrome window now"
if not errorlevel 2 (
  echo Sign in to YouTube/Google and TikTok, then CLOSE the Chrome window to continue.
  .venv\Scripts\python.exe scripts\upload.py login
)

echo.
echo ===== Setup finished =====
echo Next: double-click dashboard.bat, check the Setup check list, pick your streamers and upload method, press Save.
echo Autopilot is OFF by default and posts are Public from the start. To begin with a private trial, set "Private days at the start" in the dashboard.
choice /m "Open the dashboard now"
if not errorlevel 2 call dashboard.bat
endlocal
