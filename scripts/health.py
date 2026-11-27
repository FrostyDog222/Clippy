"""Health check: is everything Clippy needs in place? Pure logic (the dashboard gathers the facts and passes them in), so it can be tested.
checks(...) -> [{"id", "level": ok|info|warn|bad, "label", "detail", "fix": None | {"label", "action"}}]   action: signin | update | settings:<element id> | none"""


def checks(cfg, signin, update, free_gb, waiting, have, yt_limit_until=None, max_waiting=8):
    out = []

    def add(i, level, label, detail="", fix=None):
        out.append({"id": i, "level": level, "label": label, "detail": detail, "fix": fix})

    for name, key in (("YouTube", "youtube"), ("TikTok", "tiktok")):
        if cfg.get("post_" + key, True) is False:
            continue
        v = (signin or {}).get(key)
        if v is True:
            add("signin_" + key, "ok", f"Signed in to {name}")
        elif v is False:
            add("signin_" + key, "bad", f"Not signed in to {name}", "Uploads to it will fail until you sign in.", {"label": "Sign in", "action": "signin"})
        else:
            add("signin_" + key, "info", f"{name} sign-in not checked yet", "Press Check status in Settings to be sure.", {"label": "Check", "action": "settings:scb"})
    bn = have.get("browser") or "Google Chrome"
    add("chrome", "ok" if have.get("chrome") else "bad", f"{bn} found (used to sign in and upload)" if have.get("chrome") else "No browser found",
        "" if have.get("chrome") else "Uploading needs Google Chrome or Microsoft Edge (Edge comes with Windows). Install one and restart Clippy.")
    for k, label in (("ffmpeg", "ffmpeg"), ("ytdlp", "yt-dlp")):
        add(k, "ok" if have.get(k) else "bad", f"{label} found" if have.get(k) else f"{label} not found",
            "" if have.get(k) else "Run setup.bat to install it.")
    w = cfg.get("title_writer", "template")
    if w == "api":
        add("ai", "ok" if have.get("ai_key") else "warn", "AI title writer: key saved" if have.get("ai_key") else "AI title writer has no key saved",
            "" if have.get("ai_key") else "Titles fall back to the no-AI templates until you add a key (or choose another writer).", None if have.get("ai_key") else {"label": "Open AI settings", "action": "settings:keystat"})
    elif w == "claude":
        add("ai", "info", "AI title writer: Claude (CLI)", "Works only while Claude Code is signed in on this PC.")
    else:
        add("ai", "info", "No AI title writer", "Titles use the built-in templates. An AI (your own key or a local model) writes funnier ones.", {"label": "Open AI settings", "action": "settings:keystat"})
    if free_gb is not None:
        add("disk", "bad" if free_gb < 3 else "warn" if free_gb < 10 else "ok", f"{free_gb:.0f} GB free on this drive",
            "Very low: runs will fail." if free_gb < 3 else "Getting low: use Clean up now in Settings." if free_gb < 10 else "",
            {"label": "Clean up", "action": "settings:keep_clips_days"} if free_gb < 10 else None)
    if waiting >= max_waiting:
        add("waiting", "info", f"{waiting} clips are waiting for you", "Autopilot stops preparing new clips while this many wait. Look at them: rate each one (👍, 😐 or 👎), then post, schedule or remove it.")
    if update and update.get("available"):
        add("update", "info", "A new version of Clippy is available", "", {"label": "Update now", "action": "update"})
    if yt_limit_until:
        add("ytlimit", "warn", "YouTube's daily upload limit was reached", f"Clips go to TikTok only until about {yt_limit_until}; then YouTube resumes by itself.")
    return out


def summary(items):
    bad = sum(1 for x in items if x["level"] == "bad")
    warn = sum(1 for x in items if x["level"] == "warn")
    return {"bad": bad, "warn": warn, "ok": bad == 0 and warn == 0}
