# Clippy

Clippy finds the best moments in streams and videos, cuts them into vertical 1080x1920 Shorts, writes a short title and description, and posts them to **YouTube Shorts** and **TikTok**. It runs on your own Windows PC, with your own accounts and, optionally, your own AI key.

You choose a topic (a game or anything: "GTA 6", "Minecraft", "Elden Ring"), a list of streamers, or simply "whatever is trending right now". Clippy does the rest, or hands you finished clips to review first.

## What it does

- **Finds moments** from loudness jumps, excited voice, laughter, swearing, hype words, on-screen motion, YouTube's "most replayed" graph and live-chat reactions. An AI can also score the whole transcript window by window ("worth watching", funny first) and choose the exact start and end of the bit.
- **Sources:** YouTube videos and VODs, live streams (YouTube and Twitch) with chat-spike detection, Twitch top clips, and any YouTube or Twitch link you paste ("Clip a link").
- **Cuts** 1080x1920 clips with word-by-word captions (swear words are masked in the captions only, the audio is never changed), the streamer's webcam on top and the game below when a webcam is found, or a blurred background when not.
- **Writes** a short catchy title or caption, the streamer's name and hashtags, with an AI (your own key: Gemini, any OpenAI-compatible endpoint, Anthropic) or without one.
- **Ready to post:** every finished clip waits in the dashboard with a preview. Edit the title, description, hashtags and TikTok caption, then press Post (private or public). *Auto post* is OFF by default.
- **Control:** Pause (freezes the running job, Resume continues it), Stop now, a live log with timestamps, and a time estimate.

## Requirements

Windows 10 or 11, Google Chrome, Python 3.10+, ffmpeg and a JavaScript runtime for yt-dlp (Deno or Node.js). `setup.bat` checks for each and offers to install what is missing with winget.

## Start

1. Double-click `setup.bat` (installs into this folder only; asks before every download).
2. Double-click `dashboard.bat`, sign in to YouTube and TikTok once with the Sign in button, set your topic and streamers, press Save.
3. Press **Run one now** and watch the log, or open **Clip a link** to clip one specific video.

Autopilot and Auto post are OFF by default. In "Ready to post", the "Post as" choice defaults to private.

## What leaves your PC

Clippy can only connect to YouTube, TikTok, Twitch, the official Google / Gemini APIs and the one AI address you enter yourself. Everything else is blocked in code (`scripts/netguard.py`) and logged. There is no telemetry, no analytics, no server run by the authors, and no account. If you use an AI, the clip transcript, the source video's title, a few small frames and some chat messages are sent to that provider with your own key. Choose "No AI" to send nothing.

The only other connections are one-time model downloads in `setup.bat` (speech model from huggingface.co, face model from github.com/opencv) after you confirm, and the yt-dlp update check on pypi.org.

## Your secrets stay on your PC

API keys are stored encrypted with Windows DPAPI. Your YouTube and TikTok logins live in a dedicated Chrome profile inside the project folder. Never share `api_tokens/`, `chrome_profile/`, `autopost_config.json` or `autopost_state.json`; the included `.gitignore` keeps them, your downloads and your clips out of git.

Run `.venv\Scripts\python.exe scripts\security_audit.py` to scan the code for dangerous patterns and list every web address it may contact.

## Use it responsibly

You are responsible for what you post. Clip creators who allow it, credit them, and respect copyright and the rules of YouTube and TikTok. Clippy posts through a browser session of **your own** account, which platforms may restrict or limit (for example YouTube's daily upload limit); you use it at your own risk.

## Credits and license

Clippy grew out of the MIT-licensed project *gtavi-claudecode* by Bodila51 and is released under the same MIT license (see `LICENSE`). It builds on yt-dlp, faster-whisper, OpenCV (including the YuNet face-detection model from the OpenCV Zoo) and Playwright.
