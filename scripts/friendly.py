"""Plain-words versions of the errors the post step can report, for the dashboard ("Ready to post" list and the "Current run" bar).
friendly(raw_error) -> one or two sentences a normal person understands, saying what happened, whether anything was posted, and what to do."""
import re

RULES = [
    (r"PRIVACY_CHECK",
     "TikTok: the clip WAS posted, but Clippy could not confirm that it is set to Only me. Open TikTok Studio and check its privacy right now (set it to Only me if it says Everyone)."),
    (r"YOUTUBE_DAILY_LIMIT|daily upload limit|upload limit|limita de .?nc.rcare",
     "YouTube's daily upload limit has been reached, so YouTube refused the upload. Nothing was posted to YouTube. In YouTube Studio you can lift the limit once by verifying "
     "your channel (the Verify link in the upload window), or try again in about 24 hours. TikTok is not affected."),
    (r"stayed active for 30 minutes|full-screen app",
     "A full-screen app (a game or video) was in front for 30 minutes, so Clippy did not open the browser over it. Nothing was posted. Close it and press Post again."),
    (r"both platforms are switched off|switched OFF for both",
     "Both YouTube and TikTok are switched off in \"Where to post\", so there was nowhere to post. Turn one on, save, and try again."),
    (r"(youtube|tiktok): not signed in|not signed in",
     "Clippy is not signed in to that site any more, so nothing was posted. Use the sign-in check in the dashboard to log in again."),
    (r"CAPTCHA|verification",
     "The site asked for a CAPTCHA or a verification step, which only you can complete. Open the sign-in window from the dashboard, finish it, then try again. Nothing was posted."),
    (r"could not set TikTok visibility",
     "Clippy could not confirm TikTok's privacy setting, so it stopped before posting. Nothing was posted. Try again."),
    (r"Chrome debugging port|Chrome not found",
     "Clippy's browser could not start. Close any leftover Chrome windows that use Clippy's profile (or restart the PC) and try again."),
    (r"another upload is still running",
     "Another upload is still running. Wait for it to finish, then try again."),
    (r"clip not found|metadata contains a local path",
     "The clip or its text could not be used (missing file, or the text contained a file path). Nothing was posted."),
    (r"TimeoutError|Timeout \d+ms",
     "The site did not respond the way Clippy expected (slow connection, or the page changed). Nothing was posted. Try again; if it keeps happening, the details below show where it stopped."),
]


def friendly(err):
    """Friendly text for a raw error string; unknown errors are shortened but never hidden."""
    raw = str(err or "").strip()
    if not raw:
        return ""
    parts = [x.strip() for x in raw.split(" | ") if x.strip()]  # one error per platform: explain each on its own
    if len(parts) > 1:
        return " ".join(friendly(x) for x in parts)
    site = "YouTube" if raw.lower().startswith("youtube") else "TikTok" if raw.lower().startswith("tiktok") else ""
    for rx, text in RULES:
        if re.search(rx, raw, re.I):
            return (f"{site}: " if site and not text.startswith(("YouTube", "TikTok", "Both", "A full", "Another", "Clippy", "The clip", "The site", "The site asked")) else "") + text
    return (f"{site} problem: " if site else "Problem: ") + re.sub(r"\s+", " ", raw)[:220]
