<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/clippy-dark.png">
    <img src="docs/clippy-light.png" alt="Clippy, the paperclip mascot with sunglasses and a thumbs up" width="380">
  </picture>
</p>

# Clippy

Clippy finds the best moments in streams and videos, cuts them into vertical 1080x1920 Shorts, writes a short title and description, and posts them to **YouTube Shorts** and **TikTok**. It runs on your own Windows PC, with your own accounts and, optionally, your own AI key.

You choose a topic (a game or anything: "GTA 6", "Minecraft", "Elden Ring"), a list of streamers, or simply "whatever is trending right now". Clippy does the rest, or hands you finished clips to review first.

> **The most important thing to know:** Clippy gets better when **you review its clips**. Press 👍, 😐 or 👎 on every clip you look at. That is how it learns *your* taste. See [Review your clips: it is how Clippy learns](#review-your-clips-it-is-how-clippy-learns).

## What it does

- **Finds moments** from loudness jumps, excited voice, laughter, swearing, hype words, on-screen motion, YouTube's "most replayed" graph and live-chat reactions. An AI can also score the whole transcript window by window ("worth watching", funny first) and choose the exact start and end of the bit.
- **Sources:** YouTube videos and VODs, live streams (YouTube and Twitch) with chat-spike detection, Twitch top clips (the streamers on your list first, trending only as a backup), and any YouTube or Twitch link you paste ("Clip a link"). **Manual runs and Autopilot choose their source separately**: you can run videos by hand while Autopilot works on live streams only.
- **Live streams** are watched in your languages only (Clippy reads each Twitch streamer's language), and a streamer who is on two platforms is watched once. A chat spike is only a hint: every live moment goes through the same checks as a video, and live clips have their **own minimum score** (default 6, videos 7) because they are harder to get. Busy chats of big streamers are judged against their own usual spikes. The dashboard shows which streamers Clippy is watching right now (it updates live); you can swap one for a specific streamer or a random one, and a quiet or ended stream is replaced by itself.
- **Cuts** 1080x1920 clips with word-by-word captions (swear words are masked in the captions only, the audio is never changed), the streamer's webcam on top and the game below when a webcam is found, or a blurred background when not. If the source already has burned-in subtitles, Clippy does not add its own on top.
- **Writes** a short catchy title or caption, the streamer's name and hashtags, with an AI (your own key: Gemini, any OpenAI-compatible endpoint, Anthropic, or a local model) or without one.
- **Scores every clip** with a star number you can hover to see why, and lets you set a **minimum score** (default 7; Clippy warns you if you go lower, and again below 5).
- **Never makes the same moment twice from the same streamer** (other streamers playing the same game are never treated as duplicates).

## Ready to post

Every finished clip waits in the dashboard with a preview (hover to play it muted). In three tidy rows you can:

- **Post** (private or public) with one of three buttons: **Post on TikTok**, **Post on YouTube** or **Post on TikTok + YouTube**. Or **Schedule** it with a dark calendar and a 12-hour or 24-hour time picker (your choice in Settings). Scheduled clips are posted while Clippy is running; a missed time is never posted late by surprise.
- **Edit text**, ask for a **New joke**, **Trim** it (the original is kept), **Crop** it again with another layout, or press **Rebuild without captions** (also for videos that already have their own subtitles). A clip marked "language unsure" was made without captions and waits for you.
- **Rate it** 👍 / 😐 / 👎 (with a reason for 👎), or **Remove** it. "Mark as not posted" lets you post a changed version again.
- Work fast with **keyboard shortcuts** (J/K, U, N, D, X, S, E, Del), **select several clips** at once, and **spread your liked clips over the next days** while respecting your daily limit and posting window.

### How the post buttons and the filter work

Each clip remembers where it was already posted, and the buttons follow that:

- A platform's button is **greyed out once the clip is posted there**. Hover over it and it says "Already posted on TikTok" (or YouTube), so you can never upload the same clip twice by mistake. A platform you switched off in **Settings, Where to post** is greyed out too, and the hover text says so.
- **Post on TikTok + YouTube** posts to both. If the clip is already on one platform it is greyed out too, and the hover text says which one; use the single button for the platform that is still missing. If one platform fails (for example YouTube's daily upload limit), the other still goes out, and you can post the missing one later with its own button.
- Over the clip list, the **filter** puts every clip in exactly one group, so the lists never overlap: **Not posted anywhere yet**, **Scheduled**, **Posted on both YouTube and TikTok**, **Posted on YouTube only** and **Posted on TikTok only**. Choose "All clips" to see everything again. If you switch a platform off in Settings, "both" means every platform that is still switched on.

## Review your clips: it is how Clippy learns

Clippy is not trained on anyone else's taste. It learns **yours**, from the clips you rate. This is the most valuable thing you can do for it, and it takes a few seconds per clip.

| You press | What it tells Clippy |
|---|---|
| 👍 | "This one stood out, find more like it." The strongest signal. |
| 👎 | "This one stood out as bad, avoid this." Also a strong signal. You can tick why from a short list that teaches Clippy something it can act on: not funny, nothing happens, too slow, no context, bad start or end, bad crop or webcam, off topic. |
| 😐 | "I looked at it, it is fine either way." A light signal; it also tells Clippy that the clip is no longer waiting for you. |
| nothing | Almost no information. A clip you never rate teaches Clippy very little. |

**How much do you need?** Clippy starts using your taste once you have rated about **12 clips with at least 8 👍**. At first the nudge is small and grows as it becomes sure of the pattern. Later, Clippy also reads how your posted clips did (their views about 2 days after posting) as a weaker, second teacher. The more clips you rate, and the more honest you are (including the 👎), the better its choices get: which moments, which streamers, which kinds of reactions.

**Why bother?** Without ratings Clippy only uses its built-in rules. With ratings it learns things no rule can know, like which streamers fit your channel or what kind of funny you like. Clips you looked at no longer count as "waiting for you", so Autopilot keeps working instead of stopping when many clips pile up.

## Your learning file: keep it safe

What Clippy learns is stored in a few small files **on your own PC**:

| File | What is in it |
|---|---|
| `ratings.jsonl` | every 👍 / 😐 / 👎 you gave, with the signals Clippy saw in that moment (voice energy, AI score, length, streamer...) |
| `learned_weights.json` | what Clippy worked out from your ratings |
| `views.jsonl` | how your posted clips did after about 2 days |
| `seen_moments.jsonl` | which moments were already made, so the same one is not made twice |

These files are **the memory of your Clippy**, and they are worth protecting:

- **They never leave your PC and are never uploaded** (they are in `.gitignore` and not part of any release or update). Nothing is sent to the authors.
- **Deleting a clip does not delete what Clippy learned from it.** The rating is written down when you press the button (and again when you remove the clip), so you can clean up your Ready to post list freely.
- **They are lost** if you delete the Clippy folder or press **Start fresh** in the History learning card. To be safe, use **Settings, Backup, Download backup** now and then, and **Restore from a file** after a reinstall or on a new PC. The backup contains your settings and these learning files, never your sign-ins, API keys, videos or logs.

## Automation

- **Autopilot** finds and prepares clips by itself on your schedule: a posting window, posts per day, a minimum gap, and clips per run. **These limits are for automation only.** A run you start yourself (Run now, or live streams by hand) ignores all of them.
- **Auto-Post is OFF by default.** With it off, Autopilot only prepares clips for you to review. Turn it on and Clippy posts by itself.
- **It keeps working with the browser tab closed**: Clippy runs hidden in the background until you press Quit dashboard or the PC sleeps or restarts. Optionally, **Start with Windows** (only works while Autopilot is on, with a clear warning first) brings it back after a restart.
- **YouTube's daily upload limit** is remembered: until it resets, clips go to TikTok only and YouTube resumes by itself.
- **Stop now** ends a run but leaves Autopilot on (it waits about 20 minutes before its next automatic run). A failed run still switches Autopilot off, so it never retries the same error all night.
- Only **one Clippy dashboard** can run at a time, so a scheduled clip cannot be posted twice.
- **Your game keeps the focus.** Clippy posts through its own Chrome profile, but it opens its tabs in the background (no start-up window) and reads your TikTok numbers with a hidden browser, so a full-screen game or video is not pushed to the taskbar.

## Looking after itself

- A **health bar** at the top shows whether you are signed in, Chrome, ffmpeg and yt-dlp are found, your AI key works and there is disk space, with a fix button for each problem.
- Optional **Windows notifications** (off by default, local only) when a run finishes or a scheduled post is done or failed. Clicking one opens the dashboard.
- **Automatic updates** from this repository replace only program files, never your settings, clips or learning files, and **Go back to the previous version** is one click away.
- A **quick-start guide** walks new users through the first four steps.

## AI is optional

Clippy does not require an AI provider. AI integration is optional, and users can configure their own provider or local LLM. No AI credentials or user data are routed through the developer.

## Twitch clips (made by viewers)

Clips that viewers save on Twitch are usually the funniest or most interesting moments, and they are quick to process. Clippy looks at them first. In Settings, Streamers, **Twitch clips**, you can choose how far back to look (last week, last month or all time), how many clips to consider per streamer, whether to use **only** Twitch clips (no YouTube videos), and a checkmark to **prioritize viewer-made Twitch clips**: when it is on, the most viewed Twitch clips of your topic (any streamer) are looked at before any YouTube video; when it is off, everything runs in the normal order.

## What Clippy looks for

People clip many kinds of moments, and Clippy treats them all as clip-worthy: **funny** (a joke, irony, an awkward moment, a mishap or glitch, no punchline needed), **drama** (an argument, beef, a call-out, rage), **shock** (a twist, a jump scare, a shocking reveal), **skill** (an impressive play or clutch), **fail** (an epic fail, a costly mistake), **emotional** (a wholesome, proud or sad moment, a big celebration) and **interesting** (a hot take, news, a reveal). The AI names the kind of every moment and must show it: the key line quoted from the transcript, a short description of what happens, or an audible reaction; a moment with nothing to show scores low. The moment finder also listens for the words of an argument when the voice is raised. Titles follow the kind: a drama or emotional clip gets a sharp or curious title, not a joke. The learning card shows how often you liked each kind once there are a few of them.

## How Clippy judges humour

The AI that reads each moment is told to be a hard judge, and a moment can be worth watching in two ways. **Funny:** it scores high when the AI can show what is funny: a **quoted punchline** (checked against the transcript), a described **funny situation** (a mishap, a glitch or physical comedy needs no punchline, like a controller drifting while he follows someone), or an **audible reaction** (laughing, swearing, an excited voice), with a setup and a payoff. **Interesting:** something surprising, tense, impressive, revealing or remarkable is said or happens, and the AI must say what it is. An interesting moment is never marked down just because it is not funny. A bit that is neither, chaos or crashes with no point, or a funny claim with no punchline and nothing remarkable, scores low. Clips that viewers saved on Twitch are not held to the quote rule, because viewers only save a moment when something happened. Scores use one decimal and the whole range, so average clips no longer all get the same number. In **Settings**, next to "Who writes the titles", an optional switch shows the AI a few examples of **your** taste: short descriptions of clips you liked and disliked, the funny line of the liked ones and your reasons for the disliked ones. It is off by default, and only those short descriptions (never a transcript) are sent to your AI provider. The learning card also shows how often you liked each kind of clip, with a range, because a percentage from a few clips says very little.

## Videos or live streams?

We recommend **videos and VODs**: Clippy can read the whole video and go straight to the interesting parts. **Live streams work just fine too**, they just take longer: Clippy has to wait for something to happen instead of jumping to the good parts, and sometimes nothing worth clipping happens at all. It depends on the stream, but it works. Defaults: minimum score 7 for videos and 6 for streams; you can lower them (2-3 allows nearly everything) if you want more clips and accept weaker ones.

Every clip in Ready to post keeps a short raw copy without captions (about 15-80 MB) so **Crop** and **Rebuild without captions** work without downloading the video again. It is deleted when you remove or post the clip. Clips also leave Ready to post by themselves after the number of days set in Settings (Keep finished clips for, default 3, 0 = never). Each clip gets the period that was set when it was made: changing the setting later only affects new clips. This only removes the clip from the dashboard and the saved copy on your PC: videos already posted to YouTube or TikTok are never touched. Each clip shows a small countdown in its top-right corner; click it to keep that clip, or tick several clips and press Keep selected. A clip with a scheduled post is kept until it is posted. Big downloaded videos are deleted automatically after the clips are made.

## Languages

> **Heads up:** English works just fine. Other languages are not fully tested: they might fail or behave oddly (wrong or missing captions, a missed or misjudged moment, a streamer's mixed languages confusing it). Look at those clips before you post them.

Pick your languages in Settings. Clippy reads the language from the speech and only trusts it at 80% or more. A moment counts as "in your language" when at least 25% of its words are, so a streamer who switches between their language and English still works, and the captions show every word. When Clippy is not sure of the language (and the words do not look English) the clip is made without captions, marked "language unsure" and never posted by itself. Every clip has a "Rebuild without captions" button. Reaction words (shock, laughs, swearing) are built in for 15 languages. Slurs in other languages are not filtered: that is your responsibility.

## Requirements

Windows 10 or 11, Google Chrome, Python 3.10+, ffmpeg and a JavaScript runtime for yt-dlp (Deno or Node.js). `setup.bat` checks for each and offers to install what is missing with winget.

## Start

1. Double-click `setup.bat` (installs into this folder only; asks before every download).
2. Double-click `dashboard.bat`, sign in to YouTube and TikTok once with the Sign in button, set your topic and streamers, press Save.
3. Press **Run now** (choose how many clips and whether to clip videos or live streams) and watch the log, or open **Clip a link** to clip one specific video.
4. **Review the clips and rate them.** That is the step that makes Clippy yours.

Autopilot and Auto-Post are OFF by default. In "Ready to post", the "Post as" choice defaults to private.

## What leaves your PC

Clippy can only connect to YouTube, TikTok, Twitch, the official Google / Gemini APIs and the one AI address you enter yourself. Everything else is blocked in code (`scripts/netguard.py`) and logged. There is no telemetry, no analytics, no server run by the authors, and no account. If you use an AI, the clip transcript, the source video's title, a few small frames and some chat messages are sent to that provider with your own key. Choose "No AI" to send nothing. Your ratings and everything Clippy learned stay on your PC.

The only other connections are one-time model downloads in `setup.bat` (speech model from huggingface.co, face model from github.com/opencv) after you confirm, the yt-dlp update check on pypi.org, and the update check on GitHub.

## Your secrets stay on your PC

API keys are stored encrypted with Windows DPAPI. Your YouTube and TikTok logins live in a dedicated Chrome profile inside the project folder. Never share `api_tokens/`, `chrome_profile/`, `autopost_config.json`, `autopost_state.json` or your learning files; the included `.gitignore` keeps them, your downloads and your clips out of git.

Run `.venv\Scripts\python.exe scripts\security_audit.py` to scan the code for dangerous patterns and list every web address it may contact.

## Use it responsibly

You are responsible for what you post. Clippy is free software: what you clip, edit, post, and where and how you use it is entirely your own responsibility, including following the rules of YouTube, TikTok, the streamers and any copyright or content laws that apply to you. Clip creators who allow it, credit them, and respect copyright. Clippy posts through a browser session of **your own** account, which platforms may restrict or limit (for example YouTube's daily upload limit); you use it at your own risk.

## Credits and license

Clippy is released under the MIT license (see `LICENSE`), copyright FrostyDog222. It builds on yt-dlp, faster-whisper, OpenCV (including the YuNet face-detection model from the OpenCV Zoo) and Playwright.
