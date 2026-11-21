# Clippy

Finds the best GTA 6 moments (live streams and videos), cuts vertical Shorts and posts them to YouTube and TikTok. Everything runs on your own PC.

## AI is optional

Clippy does not require an AI provider. AI integration is optional, and users can configure their own provider or local LLM. No AI credentials or user data are routed through the developer.

## Review your clips: it is how Clippy learns
Clippy learns **your** taste from the clips you rate. Press 👍 (stood out, good), 😐 (looked at it, neutral) or 👎 (stood out, bad; say why) on every clip you look at. 👍 and 👎 teach the most; a clip you never rate teaches almost nothing. Clippy starts using your taste after about 12 rated clips with at least 8 👍, and gets better the more you rate. Your posted clips' views (read after about 2 days) are a weaker second teacher.

## Your learning file: keep it safe
`ratings.jsonl`, `learned_weights.json`, `views.jsonl` and `seen_moments.jsonl` are the memory of your Clippy. They stay on your PC, are never uploaded and are not part of any update or release. Deleting a clip does NOT delete what Clippy learned from it. They are lost only if you delete the Clippy folder or press "Start fresh" in the History learning card, so use Settings, Backup, Download backup now and then (it holds your settings and these files, never your sign-ins or keys).

## Start
1. Double-click `setup.bat` (installs into this folder only; asks before every download).
2. Double-click `dashboard.bat`, sign in to YouTube and TikTok once (the Sign in button), pick your streamers, press Save.
3. Autopilot is OFF by default and posts are public from the start. For a private trial set "Private days at the start".

## What leaves your PC
Clippy can only talk to: **YouTube, TikTok, Twitch**, the official **Google / YouTube / Gemini API**, the **TikTok API**, and the one AI address **you** enter (for example `api.openai.com`).
Everything else is blocked in code (`scripts/netguard.py`) and written to `netguard.log`. There is no telemetry, no analytics, no author server and no account.
If you use an AI, the clip transcript, the source video title, 3 small frames of the clip and a few live-chat messages are sent to that AI provider with **your own key**; choose "No AI" in the dashboard to send nothing.
The only other connections are one-time model downloads inside `setup.bat` (speech model from huggingface.co, face model from github.com/opencv) after you confirm, and the yt-dlp update check on pypi.org.

## Your secrets stay on your PC
API keys are stored encrypted with Windows DPAPI (only your Windows user on this PC can read them). Your YouTube/TikTok logins live in the `chrome_profile` folder. **Never give anyone `api_tokens/`, `chrome_profile/`, `autopost_config.json` or `autopost_state.json`.**

## Verify it yourself
`.venv\Scripts\python.exe scripts\security_audit.py` checks the file checksums against `SHA256SUMS.txt`, scans for dangerous code, lists every web address in the code against the allowed list, and confirms the network guard is installed and set to `enforce`.
