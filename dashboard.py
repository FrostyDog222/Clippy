"""Autopost control panel. Run dashboard.bat -> http://127.0.0.1:8765 (local only).
Autopilot ON = this process checks every minute whether a post is due (tick.py rules) and starts a hidden run. Keep it running."""
import datetime as dt, http.server, importlib.util, json, os, pathlib, re, shutil, subprocess, sys, threading, time

R = pathlib.Path(__file__).parent
os.environ["PYTHONUTF8"] = "1"  # everything the dashboard starts writes UTF-8 (emoji in titles must not crash runs)
sys.path.insert(0, str(R)); sys.path.insert(0, str(R / "scripts"))
import tick  # noqa: E402
import cleanup  # noqa: E402
import textutil  # noqa: E402
import ai_title  # noqa: E402
import secretstore  # noqa: E402
from friendly import friendly  # noqa: E402

CFG, STATE, PAUSE, LOG, PROG = (R / "autopost_config.json", R / "autopost_state.json", R / "PAUSED.txt",
                                R / "autopost_log.md", R / "progress.json")
NOWIN, DETACHED = 0x08000000, 0x00000008
HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def now_iso():
    return dt.datetime.now().isoformat(timespec="seconds")


def read_cfg():
    c = json.loads(CFG.read_text(encoding="utf-8"))
    if "ai_profiles" not in c:  # migrate the old single API fields into the first profile (key file copied, old one kept)
        c["ai_profiles"], c["ai_active"] = [], ""
        if c.get("title_api_url") or c.get("title_api_model"):
            c["ai_profiles"] = [{"id": "p1", "name": "My API", "url": c.get("title_api_url", ""), "model": c.get("title_api_model", "")}]; c["ai_active"] = "p1"
            if secretstore.exists("title_api_key") and not secretstore.exists("key_p1"):
                secretstore.save("key_p1", secretstore.load("title_api_key"))
    c.setdefault("live_watch", False); c.setdefault("live_max_streams", 3); c.setdefault("ai_check", False); c.setdefault("quality", "strict")
    return c


def save_cfg(c):
    CFG.write_text(json.dumps(c, indent=2), encoding="utf-8")


def read_posts():
    return json.loads(STATE.read_text(encoding="utf-8")).get("posts", []) if STATE.exists() else []


def set_prog(pct, msg):
    PROG.write_text(json.dumps({"pct": pct, "msg": msg, "ts": now_iso()}), encoding="utf-8")


def progress():
    return json.loads(PROG.read_text(encoding="utf-8")) if PROG.exists() else None


def _pid_alive(pid):
    try:
        import ctypes
        k = ctypes.windll.kernel32; h = k.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong(); ok = k.GetExitCodeProcess(h, ctypes.byref(code)); k.CloseHandle(h)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    except Exception:
        return True  # cannot tell: assume it is alive


def _run_pid():
    t = (R / "autorun.pid").read_text().strip()
    return int(json.loads(t)["pid"]) if t.startswith("{") else int(t)  # the file holds {"pid", "start"} (older runs: just the number)


def run_timing():
    """How long the run has been going, how long a typical run takes (from the last runs) and a rough time left. None when no run of ours is being tracked."""
    try:
        d = json.loads((R / "autorun.pid").read_text())
        if not _pid_alive(d["pid"]):
            return None
        elapsed = time.time() - float(d["start"])
    except Exception:
        return None
    try:
        hist = [h["secs"] for h in json.loads((R / "run_history.json").read_text(encoding="utf-8"))[-8:] if h.get("secs", 0) >= 90 and h.get("result") == "0"]  # only runs that finished normally count: a crashed or stopped run says nothing about how long a run takes
    except Exception:
        hist = []
    typical = sum(hist) / len(hist) if hist else None
    pct = (progress() or {}).get("pct") or 0
    slow = bool(typical and elapsed >= typical)  # past the typical length: say so instead of a made-up countdown
    eta = None if (not typical or slow) else max(60, typical - elapsed)
    if eta is not None and pct >= 50:  # already cutting / checking: only the end of the run is left
        eta = min(eta, 240 if pct < 65 else 90)
    return {"elapsed": int(elapsed), "typical": int(typical) if typical else None, "eta": int(eta) if eta else None, "slow": slow}


def run_alive():
    """Is something that reports progress still working? (the autorun process, an upload holding post.lock, a post started from Ready to post)"""
    try:
        if _pid_alive(_run_pid()):
            return True
    except Exception:
        pass
    return (R / "post.lock").exists() or any((s or {}).get("state") == "posting" for s in REVIEW_STATUS.values())


def run_stale():
    """A progress note says 'working' but nothing is running and it has been quiet for a minute: the run died without saying so."""
    p = progress()
    if not p or not (0 < p["pct"] < 100):
        return False
    return (dt.datetime.now() - dt.datetime.fromisoformat(p["ts"])).total_seconds() >= 60 and not run_alive()


def running():
    p = progress()
    return bool(p) and 0 < p["pct"] < 100 and (dt.datetime.now() - dt.datetime.fromisoformat(p["ts"])).total_seconds() < 45 * 60 and not run_stale()


def run_now(force=False):
    """Clippy's own pipeline (scripts/autorun.py, no Claude). force=True is the manual "Run now" button (ignores the daily limit / window / gap);
    the autopilot calls it without force so tick.py decides. (This used to start run_slot.bat, the old Claude-driven mode.)"""
    set_prog(1, "Starting")
    subprocess.Popen([BGPY, "scripts/autorun.py", *(["--force"] if force else [])], cwd=R, creationflags=NOWIN | DETACHED,
                     stdout=open(R / "autorun.log", "a", encoding="utf-8"), stderr=subprocess.STDOUT)


SUSP = R / "suspended.json"  # pids frozen by Pause, so Resume can unfreeze exactly those (even after a dashboard restart)


def _tree(root):
    """pid + all descendants (the run, moments.py, yt-dlp, ffmpeg ...), from a Windows process snapshot. No extra packages."""
    import ctypes
    from ctypes import wintypes
    class PE(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]
    k = ctypes.windll.kernel32; snap = k.CreateToolhelp32Snapshot(0x2, 0); kids = {}
    e = PE(); e.dwSize = ctypes.sizeof(PE)
    ok = k.Process32FirstW(snap, ctypes.byref(e))
    while ok:
        kids.setdefault(e.th32ParentProcessID, []).append(e.th32ProcessID); ok = k.Process32NextW(snap, ctypes.byref(e))
    k.CloseHandle(snap)
    out, todo = [], [int(root)]
    while todo:
        p = todo.pop()
        if p not in out:
            out.append(p); todo += kids.get(p, [])
    return out


def _suspend(pids, resume=False):
    import ctypes
    k = ctypes.windll.kernel32; nt = ctypes.windll.ntdll; n = 0
    for pid in pids:
        h = k.OpenProcess(0x0800, False, int(pid))  # PROCESS_SUSPEND_RESUME
        if h:
            (nt.NtResumeProcess if resume else nt.NtSuspendProcess)(h); k.CloseHandle(h); n += 1
    return n


def run_suspended():
    try:
        return any(_pid_alive(p) for p in json.loads(SUSP.read_text())["pids"])
    except Exception:
        return False


def pause_run():
    """Pause: freeze the running run (and everything it started) exactly where it is, and stop new runs from starting. Resume continues it."""
    PAUSE.write_text("paused from dashboard\n")
    try:
        root = _run_pid()
    except Exception:
        return "Paused: no run is going right now, so nothing is frozen; new runs will not start until you press Resume."
    if not _pid_alive(root):
        return "Paused (no run was active)."
    if (R / "post.lock").exists():
        PAUSE.unlink(missing_ok=True)
        raise RuntimeError("An upload is running right now: freezing it halfway could break it. Wait until it finishes, or use Stop now.")
    pids = _tree(root)
    SUSP.write_text(json.dumps({"pids": pids}))
    _suspend(pids)
    return f"Paused: the run is frozen ({len(pids)} processes) and continues when you press Resume."


def resume_run():
    try:
        pids = [p for p in json.loads(SUSP.read_text())["pids"] if _pid_alive(p)]
        _suspend(pids, resume=True)
    except Exception:
        pass
    SUSP.unlink(missing_ok=True); PAUSE.unlink(missing_ok=True)


def stop_runs():
    """Stop now: kill the run and everything it started (downloads, analysis, cutting, uploads) for good: there is no resuming it (that is what Pause is for).
    Autopilot and live mode are switched off so nothing starts again by itself."""
    c = read_cfg(); c["autopilot"] = False; c["live_watch"] = False; save_cfg(c)
    try:  # the whole tree under the run (moments, yt-dlp, ffmpeg ... even the ones the name search below cannot see)
        subprocess.run(["taskkill", "/PID", str(_run_pid()), "/T", "/F"], creationflags=NOWIN, capture_output=True)
    except Exception:
        pass
    ps = ("Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne %d -and $_.CommandLine -match "
          "'AUTOPOST\\.md|run_slot\\.bat|serve_file\\.py|gtavi-clipdesk.*(autorun|cut|moments|check|live_watch|live|post_clip|upload|stats)\\.py' } | "
          "ForEach-Object { taskkill /PID $_.ProcessId /T /F | Out-Null }" % os.getpid())
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], creationflags=NOWIN)
    for f in (R / "autorun.pid", R / "post.lock", SUSP):
        f.unlink(missing_ok=True)
    for s in REVIEW_STATUS.values():
        if (s or {}).get("state") == "posting":
            s.update(state="error", msg="Stopped by you.")
    PAUSE.unlink(missing_ok=True)
    set_prog(-1, "Stopped by you: the run was ended (it cannot be resumed; press Run one now to start a new one)")


CUSTOM_URL = re.compile(r"^https://((www\.|m\.)?youtube\.com/(watch\?v=|shorts/|live/)[\w-]{6,}|youtu\.be/[\w-]{6,}|(www\.)?twitch\.tv/videos/\d+|clips\.twitch\.tv/[\w-]+|(www\.)?twitch\.tv/\w+/clip/[\w-]+)([&?#/][^\s]*)?$")


def run_custom(url, clips=1, start=None, length=None, relaxed=False):
    """Clip one link the user chose (a YouTube video / Short or a Twitch VOD / clip): the normal pipeline, but on that source only."""
    url = str(url).strip()
    if not CUSTOM_URL.match(url):
        raise ValueError("That link is not supported. Use a YouTube video / Short link, a Twitch VOD (twitch.tv/videos/123...) or a Twitch clip link.")
    clips = max(1, min(5, int(clips or 1)))
    args = [BGPY, "scripts/autorun.py", "--force", "--url", url, "--clips", str(clips)]
    for flag, val, hi in (("--cstart", start, 100000), ("--clen", length, 600)):
        if val not in (None, ""):
            v = int(val)
            if not 0 <= v <= hi:
                raise ValueError("Start and length are minutes: start 0 to 100000, length 1 to 600.")
            args += [flag, str(v)]
    if relaxed:
        args.append("--relaxed")
    set_prog(1, "Starting (custom link)")
    subprocess.Popen(args, cwd=R, creationflags=NOWIN | DETACHED, stdout=open(R / "autorun.log", "a", encoding="utf-8"), stderr=subprocess.STDOUT)


_cli = {"t": 0, "ok": False}


def claude_logged_in():
    """claude auth status, cached 60 s (the page polls every 5 s)."""
    if time.time() - _cli["t"] > 60:
        try:
            r = subprocess.run(["claude", "auth", "status"], capture_output=True, text=True, timeout=10, creationflags=NOWIN)
            _cli["ok"] = bool(json.loads(r.stdout).get("loggedIn"))
        except Exception:
            _cli["ok"] = False
        _cli["t"] = time.time()
    return _cli["ok"]


def checks():
    mods = importlib.util.find_spec("faster_whisper") is not None
    extra = [("Claude CLI signed in (run: claude auth login)", claude_logged_in())] if read_cfg().get("uploader", "claude") == "claude" else []
    try:  # the network guard: only YouTube / TikTok / Twitch / official APIs may be contacted (scripts/netguard.py)
        sc = R / ".venv" / "Lib" / "site-packages" / "sitecustomize.py"
        extra.append(("Network guard on: nothing but YouTube, TikTok, Twitch and official APIs can be reached",
                      sc.exists() and "netguard" in sc.read_text(encoding="utf-8", errors="ignore") and read_cfg().get("network_guard", "enforce") == "enforce"))
    except Exception:
        pass
    try:  # the headless-browser test (a real sign-in check done once): TikTok accepts a hidden browser, YouTube Studio does not
        hc = json.loads((R / "headless_check.json").read_text(encoding="utf-8"))
        extra.append(("Headless browser (no window), TikTok: works, but not used (both sites share one browser)", bool(hc.get("tiktok"))))
        extra.append(("Headless browser (no window), YouTube: NOT supported, YouTube refuses it, so uploads use a normal window placed off-screen and wait while a full-screen app is active", bool(hc.get("youtube"))))
    except Exception:
        pass
    try:  # YouTube asked Clippy to "confirm you are not a bot": video downloads are paused for a while (see scripts/autorun.py)
        left = int(json.loads((R / "yt_block.json").read_text(encoding="utf-8"))["until"] - time.time())
        if left > 0:
            extra.append((f"YouTube is blocking video downloads (bot check), pausing {left // 60} more min", False))
    except Exception:
        pass
    return extra + [("ffmpeg installed", bool(shutil.which("ffmpeg"))), ("JavaScript runtime for yt-dlp (Deno or Node.js)", bool(shutil.which("deno") or shutil.which("node"))), ("yt-dlp installed", importlib.util.find_spec("yt_dlp") is not None or bool(shutil.which("yt-dlp"))),
            ("Claude CLI installed", bool(shutil.which("claude"))), ("Python packages installed", mods),
            ("At least 1 streamer in the list", bool(read_cfg()["creators"])),
            ("Not paused", not PAUSE.exists())] + ([("AI frame check can run (title writer must be Claude signed in, or your own API)", (read_cfg().get("title_writer") == "claude" and claude_logged_in()) or (read_cfg().get("title_writer") == "api" and all(ai_title.api_settings(read_cfg())[:2])))] if read_cfg().get("ai_check") else [])


SIGNIN = R / "signin_status.json"
VPY = str(R / ".venv" / "Scripts" / "python.exe")
BGPY = str(R / ".venv" / "Scripts" / "pythonw.exe")  # every background launch uses the no-console Python: a visible console window (that closing would kill the run) can never appear
_busy = {"what": None}


def signin_state():
    s = json.loads(SIGNIN.read_text(encoding="utf-8")) if SIGNIN.exists() else {"youtube": None, "tiktok": None, "ts": None}
    s["busy"] = _busy["what"]
    return s


def _bg(what, args, flags):
    def w():
        try:
            subprocess.run([BGPY, "scripts/upload.py", *args], cwd=R, creationflags=flags, timeout=900)
        finally:
            _busy["what"] = None
    _busy["what"] = what
    threading.Thread(target=w, daemon=True).start()


def start_signin():
    """Open the dedicated Chrome window so the user signs in; when it is closed, re-check status automatically."""
    if _busy["what"] or running():
        return "Busy: wait for the current run or check to finish"
    def w():
        try:
            subprocess.run([BGPY, "scripts/upload.py", "login"], cwd=R, timeout=3600)
        finally:
            _busy["what"] = None
        start_check()
    _busy["what"] = "login"
    threading.Thread(target=w, daemon=True).start()


def start_check():
    if _busy["what"] or running():
        return "Busy: wait for the current run or sign-in to finish"
    _bg("check", ["status"], NOWIN)


def autopilot_loop():
    while True:
        time.sleep(60)
        try:
            c = read_cfg(); p = progress()
            if not c.get("autopilot"):
                continue
            if blocked_msg(c):  # list emptied (or trending switched off) while running: stop the bot
                c["autopilot"] = False; save_cfg(c); continue
            if p and p["pct"] < 0:  # last run failed or was stopped: switch off instead of retrying blindly
                c["autopilot"] = False; save_cfg(c); continue
            if running() or c.get("live_watch"):  # live mode ON: the live watcher posts, videos are not used
                continue
            ok, _, _ = tick.decide(c, read_posts(), dt.datetime.now(), PAUSE.exists())
            if ok:
                run_now()
        except Exception:
            pass


def perf():
    """Performance table + 'what works' lines from the numbers stats.py stored on each post."""
    posts = [p for p in read_posts() if not p.get("deleted")]
    rows = []
    for p in posts:
        s = (p.get("stats") or [{}])[-1]
        yt, tt = s.get("yt"), s.get("tt")
        rows.append({"ts": p.get("ts", ""), "title": p.get("title") or p.get("caption") or "", "yt_link": p.get("youtube_link"), "tt_status": p.get("tiktok_status"), "who": p.get("channel") or p.get("creator") or "?", "layout": p.get("layout") or "?", "origin": p.get("origin") or "video",
                     "seconds": p.get("seconds"), "mode": p.get("mode"), "yt": yt, "tt": tt, "views": ((yt or {}).get("views") or 0) + ((tt or {}).get("views") or 0),
                     "has": bool(yt or tt), "hour": int(p["ts"][11:13]) if p.get("ts") else None, "seen": s.get("ts")})
    have = [r for r in rows if r["has"]]
    ins = []
    if len(have) >= 5:
        def best(key, label, fmt=lambda v: v):
            g = {}
            for r in have:
                if r[key] not in (None, "?"):
                    g.setdefault(r[key], []).append(r["views"])
            g = {k: sum(v) / len(v) for k, v in g.items() if len(v) >= 2}
            if len(g) >= 2:
                k, v = max(g.items(), key=lambda kv: kv[1]); ins.append(f"Best {label}: {fmt(k)} (about {int(v)} views per post)")
        best("who", "streamer"); best("layout", "layout"); best("origin", "source", lambda v: "live streams" if v == "live" else "videos")
        best("hour", "posting hour", lambda h: f"{int(h):02d}:00")
    return {"rows": list(reversed(rows))[:300], "insights": ins, "need": max(0, 5 - len(have)), "updated": max((r["seen"] or "" for r in rows), default="")}


def stats_loop():
    """Refresh the numbers every 6 hours while the dashboard runs (never while a run is posting)."""
    time.sleep(120)
    while True:
        try:
            if not running() and not _busy["what"] and any(not p.get("deleted") for p in read_posts()):
                subprocess.run([BGPY, "scripts/stats.py"], cwd=R, creationflags=NOWIN, timeout=900)
        except Exception:
            pass
        time.sleep(6 * 3600)


LIVE_STATUS = R / "live_status.json"
_live = {"p": None}


def live_status():
    """Status written by live_watch.py; None when the watcher is not running (file older than 90 s)."""
    try:
        s = json.loads(LIVE_STATUS.read_text(encoding="utf-8"))
        return s if (dt.datetime.now() - dt.datetime.fromisoformat(s["ts"])).total_seconds() < 90 else None
    except Exception:
        return None


def live_loop():
    """Keeps the live watcher process running while the switch is ON (it exits by itself when switched off)."""
    while True:
        time.sleep(20)
        try:
            if read_cfg().get("live_watch") and not PAUSE.exists() and (_live["p"] is None or _live["p"].poll() is not None) and not live_status():
                _live["p"] = subprocess.Popen([BGPY, "scripts/live_watch.py"], cwd=R, creationflags=NOWIN,
                                              stdout=open(R / "live_watch.log", "a", encoding="utf-8"), stderr=subprocess.STDOUT)
        except Exception:
            pass


def blocked_msg(c):
    """The bot cannot run when it has no source at all: empty streamer list AND trending off."""
    if not c.get("post_youtube", True) and not c.get("post_tiktok", True):
        return "Posting is switched OFF for both YouTube and TikTok, so the bot has nowhere to post. Turn on at least one in \"Where to post\"."
    if not c.get("creators") and not c.get("use_trending_fallback") and not c.get("trending_any"):
        return ("There are no streamers in your list and the trending option is OFF, so the bot has nothing to post. "
                "Add at least one streamer or turn on 'Use trending videos as a backup'.")
    return None


def clean(new, old):
    out = dict(old)
    out["cleanup_sources"] = bool(new.get("cleanup_sources", True))
    out["clip_seconds"] = int(new.get("clip_seconds", 30)); assert 10 <= out["clip_seconds"] <= 60, "clip length must be 10 to 60 seconds"
    out["live_max_streams"] = int(new.get("live_max_streams", 3)); assert 1 <= out["live_max_streams"] <= 5, "streams to watch must be 1 to 5"
    assert new.get("title_writer", "template") in ("template", "claude", "api"); out["title_writer"] = new.get("title_writer", "template")
    url = str(new.get("title_api_url", "")).strip()
    assert not url or re.match(r"^(https://|http://(127\.0\.0\.1|localhost)[:/])", url), "API address must start with https:// (http:// only for localhost)"
    out["title_api_url"] = url; out["title_api_model"] = str(new.get("title_api_model", "")).strip()[:100]
    langs = new.get("languages", ["en"]); langs = [] if langs == "any" else ([langs] if isinstance(langs, str) else langs)
    assert all(x in textutil.LANGS for x in langs), "unknown language code"; out["languages"] = list(dict.fromkeys(langs))
    assert new.get("layout", "auto") in ("auto", "facecam", "blur"); out["layout"] = new.get("layout", "auto")
    for k in ("posts_per_day", "min_gap_minutes", "max_per_creator_per_day", "private_days", "keep_clips_days"):
        new.setdefault(k, 3 if k == "keep_clips_days" else 0)
        out[k] = int(new[k]); assert 0 <= out[k] <= 1440, k
    for k in ("window_start", "window_end"):
        assert HHMM.match(new[k]), f"{k} must be HH:MM"; out[k] = new[k]
    assert new["creator_mode"] in ("rotate", "random", "fixed"); out["creator_mode"] = new["creator_mode"]
    assert re.match(r"^\d{4}-\d{2}-\d{2}$", new["start_date"]); out["start_date"] = new["start_date"]
    out["creators"] = [str(s)[:60] for s in new["creators"]][:50]
    out["use_trending_fallback"] = bool(new["use_trending_fallback"])
    out["trending_any"] = bool(new.get("trending_any", False))
    out["auto_post"] = bool(new.get("auto_post", False))  # OFF (default): finished clips wait in "Ready to post" until the Post button is pressed
    out["post_youtube"] = bool(new.get("post_youtube", True)); out["post_tiktok"] = bool(new.get("post_tiktok", True))  # where to post
    out["topic"] = re.sub(r"\s+", " ", str(new.get("topic", "GTA 6"))).strip()[:60]  # what to look for (a game or any subject); empty = no topic
    out["stop_on_strike"] = bool(new.get("stop_on_strike", True))
    out["ai_check"] = bool(new.get("ai_check", False))
    assert new.get("quality", "strict") in ("relaxed", "strict"); out["quality"] = new.get("quality", "strict")
    profs = []
    for p in (new.get("ai_profiles") or [])[:10]:
        pid = str(p.get("id", ""))
        assert re.match(r"^[a-z0-9_-]{1,24}$", pid), "bad profile id"
        pu = str(p.get("url", "")).strip()
        assert not pu or re.match(r"^(https://|http://(127\.0\.0\.1|localhost)[:/])", pu), "API address must start with https:// (http:// only for localhost)"
        profs.append({"id": pid, "name": str(p.get("name", ""))[:40] or "Profile", "url": pu, "model": str(p.get("model", "")).strip()[:100]})
    out["ai_profiles"] = profs; out["ai_active"] = new.get("ai_active") if any(p["id"] == new.get("ai_active") for p in profs) else (profs[0]["id"] if profs else "")
    act = next((p for p in profs if p["id"] == out["ai_active"]), None)
    for k in ("ai_check_profile", "ai_title_profile"):  # which profile each job uses first ("" = the selected one)
        out[k] = new.get(k, "") if any(p["id"] == new.get(k) for p in profs) else ""
    out["title_api_url"], out["title_api_model"] = (act["url"], act["model"]) if act else ("", "")  # kept in sync for older code paths
    assert new.get("uploader", "claude") in ("claude", "script", "api"); out["uploader"] = new.get("uploader", "claude")
    return out




REVIEW = R / "review"  # finished clips waiting for the user's Post button (written by autorun.py when "Post videos automatically" is OFF)
REVIEW_STATUS = {}  # stem -> {"state": "posting" | "done" | "error", "msg": text}
STEM = re.compile(r"^\d+__clip_[A-Za-z0-9_-]+$")


def review_unlink(f, tries=6):
    """Delete a file; on Windows a video the page is still playing is locked for a moment: retry. Returns True when it is gone."""
    for _ in range(tries):
        try:
            f.unlink(missing_ok=True); return True
        except PermissionError:
            time.sleep(0.5)
    return False


def review_purge():
    """Remove leftovers of clips that were removed from the list but whose video was locked at that moment (a video without its info file, older than 10 minutes)."""
    if not REVIEW.exists():
        return
    for f in REVIEW.glob("*__clip_*.mp4"):
        try:
            if not f.with_suffix(".json").exists() and time.time() - max(f.stat().st_ctime, f.stat().st_mtime) > 600:
                for g in REVIEW.glob(f.stem + ".*"):
                    review_unlink(g, 1)
        except Exception:
            pass
    for f in REVIEW.glob("*__meta_*.json"):  # a title file whose clip is gone
        try:
            if not f.with_name(f.name.replace("meta_", "clip_")).exists() and time.time() - max(f.stat().st_ctime, f.stat().st_mtime) > 600:
                review_unlink(f, 1)
        except Exception:
            pass
    for f in REVIEW.glob("*__clip_*.posted.json"):
        if not f.with_name(f.name.replace(".posted.json", ".json")).exists() and not f.with_name(f.name.replace(".posted.json", ".mp4")).exists():
            review_unlink(f, 1)


TAG_TAIL = re.compile(r"(?:\s*#\w+)+\s*$")  # hashtags at the end of a title
TAG_OK = re.compile(r"^#\w{1,40}$")
LOCAL_PATH = re.compile(r"[A-Za-z]:[\\/]|[\\/]Users[\\/]|jobs[\\/]|file://|\.mp4\b|\bclip_\d|\bsource\.", re.I)  # same guard as post_clip.py: a file path must never reach a post


def split_meta(meta):
    """(title, description, hashtags) as the editor shows them, taken apart from the stored YouTube title / description."""
    t = meta.get("yt_title", "")
    m = TAG_TAIL.search(t)
    title = (t[:m.start()] if m else t).strip()
    lines = meta.get("yt_description", "").rstrip().split("\n")
    tags = []
    if lines and lines[-1].strip().startswith("#"):
        tags = re.findall(r"#\w+", lines[-1]); lines = lines[:-1]
    return title, "\n".join(lines).strip(), tags


def review_edit(stem, title, desc, tags, tiktok=""):
    """Save the user's title / description / hashtags into the clip's meta file; post_clip.py then uses them as they are. Raises ValueError with a plain message."""
    title, desc = re.sub(r"\s+", " ", str(title)).strip(), str(desc).replace("\r", "").strip()
    m = TAG_TAIL.search(title)
    if m and m.start() > 0:
        title = title[:m.start()].strip()  # hashtags typed at the end of the title are dropped: they are added from the Hashtags field
    tags = [("#" + str(t).strip().lstrip("#")) for t in tags if str(t).strip()]
    if not title or len(title) > 85:
        raise ValueError("The title must be 1 to 85 characters (hashtags are added after it).")
    if len(desc) > 2000:
        raise ValueError("The description is too long (2000 characters at most).")
    if len(tags) > 12 or not all(TAG_OK.match(t) for t in tags):
        raise ValueError("Hashtags: up to 12, letters, numbers and underscores only, separated by spaces (for example #GTA6 #Shorts).")
    if LOCAL_PATH.search(title + " " + desc):
        raise ValueError("The text looks like it contains a file path or file name, which must never be posted. Remove it.")
    mf = REVIEW / (stem.replace("clip_", "meta_") + ".json")
    meta = json.loads(mf.read_text(encoding="utf-8")) if mf.exists() else {}
    first = next((t for t in tags if t.lower() != "#shorts"), "")
    suffix = (first + " #Shorts").strip()
    tagline = " ".join(tags)
    tiktok = re.sub(r"\s+", " ", str(tiktok or "")).strip()
    if LOCAL_PATH.search(tiktok):
        raise ValueError("The TikTok caption looks like it contains a file path or file name, which must never be posted. Remove it.")
    streamer = next((x.strip() for x in desc.splitlines() if x.strip().startswith("\U0001F3AE")), "")  # the "🎮 Name" line
    base = tiktok or (title + (" " + streamer if streamer else ""))  # TikTok has no title, only a caption: by default it is the TITLE (+ the streamer), unless the user wrote their own
    meta["yt_title"] = f"{title} {suffix}"[:100]
    meta["yt_description"] = desc + ("\n\n" + tagline if tagline else "")
    meta["tiktok_caption"] = (base[: max(0, 150 - len(tagline) - 1)] + " " + tagline).strip()
    meta["tiktok_custom"] = tiktok
    meta["edited"] = True
    mf.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def review_items():
    review_purge()
    out = []
    for f in (sorted(REVIEW.glob("*__clip_*.json"), reverse=True) if REVIEW.exists() else []):
        if f.name.endswith(".posted.json") or not f.with_suffix(".mp4").exists() or not STEM.match(f.stem):
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            mf = REVIEW / (f.stem.replace("clip_", "meta_") + ".json")
            meta = json.loads(mf.read_text(encoding="utf-8")) if mf.exists() else {}
            sf = REVIEW / (f.stem + ".posted.json")
            et, ed, eg = split_meta(meta)
            out.append({"id": f.stem, "title": meta.get("yt_title", ""), "desc": meta.get("yt_description", ""), "caption": meta.get("tiktok_caption", ""),
                        "edit_title": et, "edit_desc": ed, "edit_tags": " ".join(eg), "edit_tiktok": meta.get("tiktok_custom", ""), "edited": bool(meta.get("edited")),
                        "seconds": d.get("seconds"), "layout": d.get("layout"), "creator": d.get("source_channel"), "source": d.get("source_title"),
                        "posted": json.loads(sf.read_text(encoding="utf-8")) if sf.exists() else {}, "status": REVIEW_STATUS.get(f.stem)})
        except Exception:
            continue
    return out


def review_post(stem, mode="private"):
    """Post one review clip (in a thread): only to the platforms that are switched on and not done yet. One upload at a time (same lock file as autorun / live)."""
    st = REVIEW_STATUS[stem] = {"state": "posting", "msg": "Waiting for the upload slot..."}
    lock = R / "post.lock"
    try:
        end = time.time() + 1800
        while True:
            try:
                os.close(os.open(lock, os.O_CREAT | os.O_EXCL)); break
            except FileExistsError:
                if time.time() - lock.stat().st_mtime > 3600:
                    lock.unlink(missing_ok=True)
                elif time.time() > end:
                    raise TimeoutError("another upload is still running")
                time.sleep(3)
        try:
            c, side = read_cfg(), REVIEW / (stem + ".posted.json")
            done = json.loads(side.read_text(encoding="utf-8")) if side.exists() else {}
            want = [p for p, k in (("youtube", "post_youtube"), ("tiktok", "post_tiktok")) if c.get(k, True) and not done.get(p)]
            if not want:
                st.update(state="done", msg="Already posted to the selected platforms."); return
            st["msg"] = "Uploading to " + " and ".join(w.capitalize() for w in want) + "..."
            r = subprocess.run([BGPY, "scripts/post_clip.py", str(REVIEW / (stem + ".mp4")), "--mode", mode] + (["--only", want[0]] if len(want) == 1 else []),
                               cwd=R, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3000, creationflags=NOWIN)
            try:
                res = json.loads(r.stdout.strip().splitlines()[-1])
            except Exception:
                res = {"error": ((r.stdout or "") + (r.stderr or ""))[-200:] or "no answer from the post step"}
            for k in ("youtube", "tiktok"):
                if res.get(k):
                    done[k] = res[k]
            side.write_text(json.dumps(done), encoding="utf-8")
            ok_now = [k.capitalize() for k in ("youtube", "tiktok") if res.get(k)]  # what DID go out this time (the other platform may have failed)
            st.update(state="error" if res.get("error") else "done",
                      msg=(("Posted to " + " and ".join(ok_now) + ". " if ok_now else "") + friendly(res.get("error"))) if res.get("error") else "Posted.", detail=(res.get("error") or "")[:300])
        finally:
            lock.unlink(missing_ok=True)
    except Exception as e:
        st.update(state="error", msg=str(e)[:300])


class H(http.server.BaseHTTPRequestHandler):
    def send(self, code, body, ctype="application/json"):
        b = body.encode("utf-8")
        self.send_response(code); self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def serve_review(self, stem):
        f = REVIEW / (stem + ".mp4")
        if not f.exists():
            return self.send(404, "no", "text/plain")
        size = f.stat().st_size; a, b, code = 0, size - 1, 200
        rng = self.headers.get("Range") or ""
        if rng.startswith("bytes="):
            x, _, y = rng[6:].partition("-")
            try:
                a = int(x or 0); b = min(int(y), size - 1) if y else size - 1; code = 206
            except ValueError:
                a, b, code = 0, size - 1, 200
        self.send_response(code); self.send_header("Content-Type", "video/mp4"); self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(b - a + 1))
        if code == 206:
            self.send_header("Content-Range", f"bytes {a}-{b}/{size}")
        self.end_headers()
        try:
            with open(f, "rb") as fh:
                fh.seek(a); left = b - a + 1
                while left > 0:
                    chunk = fh.read(min(262144, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk); left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        m = re.match(r"^/review/(\d+__clip_[A-Za-z0-9_-]+)\.mp4$", self.path.split("?")[0])
        if m and self.headers.get("Host", "").split(":")[0] in ("127.0.0.1", "localhost"):
            return self.serve_review(m.group(1))
        if self.path == "/api/state":
            c, posts = read_cfg(), read_posts()
            today = dt.date.today().isoformat()
            ok, why, mode = tick.decide(c, posts, dt.datetime.now(), PAUSE.exists())
            log = LOG.read_text(encoding="utf-8")[-1500:] if LOG.exists() else ""
            return self.send(200, json.dumps({"config": c, "paused": PAUSE.exists(), "checks": checks(),
                                              "today": [p for p in posts if p.get("date") == today and not p.get("deleted")], "log": log,
                                              "progress": progress(), "signin": signin_state(), "jobs_mb": cleanup.jobs_mb(), "all_languages": textutil.LANGS, "perf": perf(), "ai_keys": {p["id"]: secretstore.exists(f"key_{p['id']}") for p in c.get("ai_profiles", [])}, "blocked": blocked_msg(c), "live": live_status(),
                                              "review": review_items(), "run_stale": run_stale(), "run_timing": run_timing(), "run_suspended": run_suspended(),
                                              "runlog": (R / "autorun.log").read_text(encoding="utf-8", errors="replace")[-8000:] if (R / "autorun.log").exists() else "",
                                              "why": "Next run is due now" if ok else "Waiting: " + why,
                                              "mode": tick.decide({**c, "posts_per_day": 99, "window_start": "00:00", "window_end": "23:59", "min_gap_minutes": 0}, [], dt.datetime.now())[2]}))
        self.send(200, (R / "dashboard.html").read_text(encoding="utf-8"), "text/html")

    def do_POST(self):
        if self.headers.get("Host", "").split(":")[0] not in ("127.0.0.1", "localhost"):  # DNS-rebinding guard
            return self.send(403, "bad host", "text/plain")
        org = self.headers.get("Origin")  # cross-site request guard: a web page you happen to have open must not be able to POST to this local dashboard
        if org and org not in ("http://127.0.0.1:8765", "http://localhost:8765"):
            return self.send(403, "bad origin", "text/plain")
        n = int(self.headers.get("Content-Length", 0))
        if self.path == "/api/pause":  # a real pause: freezes the running run (Resume unfreezes it); also holds back new runs
            if PAUSE.exists():
                resume_run()
            else:
                try:
                    pause_run()
                except RuntimeError as e:
                    return self.send(409, str(e), "text/plain")
        elif self.path == "/api/clearlog":
            (R / "autorun.log").write_text("", encoding="utf-8")
        elif self.path == "/api/custom":
            if PAUSE.exists():
                return self.send(409, "Paused: press Resume first", "text/plain")
            if running():
                return self.send(409, "A run is in progress: wait until it finishes (or press Stop now)", "text/plain")
            try:
                b = json.loads(self.rfile.read(n))
                run_custom(b.get("url", ""), b.get("clips", 1), b.get("start"), b.get("length"), bool(b.get("relaxed")))
            except (ValueError, TypeError) as e:
                return self.send(400, str(e), "text/plain")
        elif self.path == "/api/autopilot":
            c = read_cfg(); c["autopilot"] = not c.get("autopilot", False)
            if c["autopilot"] and blocked_msg(c):
                return self.send(409, blocked_msg(c), "text/plain")
            if c["autopilot"]:
                PAUSE.unlink(missing_ok=True)
                if (progress() or {}).get("pct", 0) < 0:
                    set_prog(0, "Autopilot on")
            save_cfg(c)
        elif self.path == "/api/live":
            c = read_cfg(); c["live_watch"] = not c.get("live_watch", False)
            if c["live_watch"] and PAUSE.exists():
                return self.send(409, "Paused: press Pause / resume first", "text/plain")
            save_cfg(c)
        elif self.path == "/api/run":
            if PAUSE.exists():
                return self.send(409, "Paused: press Pause / resume first", "text/plain")
            if read_cfg().get("live_watch"):
                return self.send(409, "Live mode is ON: the bot only uses live streams. Turn 'Live chat watching' off to post from videos.", "text/plain")
            if blocked_msg(read_cfg()):
                return self.send(409, blocked_msg(read_cfg()), "text/plain")
            run_now(True)  # the manual button: run now even outside the schedule
        elif self.path in ("/api/signin", "/api/signcheck"):
            err = start_signin() if self.path == "/api/signin" else start_check()
            if err:
                return self.send(409, err, "text/plain")
        elif self.path == "/api/titlekey":  # write-only: the key is stored locally, never sent back to the page
            k = json.loads(self.rfile.read(n)).get("key", "").strip()
            secretstore.save("title_api_key", k) if k else secretstore.delete("title_api_key")
        elif self.path == "/api/stats":
            if _busy["what"] or running():
                return self.send(409, "Busy: wait for the current run or sign-in check to finish", "text/plain")
            def w():
                try:
                    subprocess.run([BGPY, "scripts/stats.py"], cwd=R, creationflags=NOWIN, timeout=900)
                finally:
                    _busy["what"] = None
            _busy["what"] = "stats"; threading.Thread(target=w, daemon=True).start()
        elif self.path == "/api/aikey":  # write-only, per profile
            j = json.loads(self.rfile.read(n)); pid = str(j.get("id", "")); k = str(j.get("key", "")).strip()
            if not re.match(r"^[a-z0-9_-]{1,24}$", pid):
                return self.send(400, "bad profile", "text/plain")
            secretstore.save(f"key_{pid}", k) if k else secretstore.delete(f"key_{pid}")  # encrypted for this Windows user only
        elif self.path in ("/api/models", "/api/aitest"):
            j = json.loads(self.rfile.read(n)); pid = str(j.get("id", "")); url = str(j.get("url", "")).strip().rstrip("/")
            if not re.match(r"^[a-z0-9_-]{1,24}$", pid):
                return self.send(400, "bad profile", "text/plain")
            key = secretstore.load(f"key_{pid}")
            try:
                if self.path == "/api/models":
                    return self.send(200, json.dumps({"models": ai_title.list_models(url, key)}))
                t0 = time.time(); ans = ai_title.complete(url, str(j.get("model", "")).strip(), key, "Reply with the single word OK.", max_tokens=300, temperature=0, timeout=90)
                return self.send(200, json.dumps({"ok": True, "text": ans.strip()[:60], "ms": int((time.time() - t0) * 1000)}))
            except Exception as e:
                msg = re.sub(r"(key|token)=[^&\s]+", r"\1=***", str(e))[:240]
                return self.send(200, json.dumps({"ok": False, "error": msg}))
        elif self.path == "/api/cleanup":
            if running():
                return self.send(409, "A run is in progress: wait until it finishes", "text/plain")
            cleanup.run()
        elif self.path == "/api/stop":
            stop_runs()
        elif self.path == "/api/quit":
            self.send(200, "{}"); threading.Thread(target=self.server.shutdown).start(); return
        elif self.path == "/api/review/edit":
            try:
                body = json.loads(self.rfile.read(n)); stem = body["id"]; assert STEM.match(stem) and (REVIEW / (stem + ".mp4")).exists()
            except Exception:
                return self.send(400, "bad id", "text/plain")
            if (REVIEW_STATUS.get(stem) or {}).get("state") == "posting":
                return self.send(409, "This clip is being posted right now: wait until it is done", "text/plain")
            try:
                review_edit(stem, body.get("title", ""), body.get("description", ""), str(body.get("hashtags", "")).split(), body.get("tiktok", ""))
            except ValueError as e:
                return self.send(400, str(e), "text/plain")
        elif self.path in ("/api/review/post", "/api/review/remove"):
            try:
                body = json.loads(self.rfile.read(n)); stem = body["id"]; assert STEM.match(stem)
                vis = body.get("mode", "private"); assert vis in ("private", "public")
            except Exception:
                return self.send(400, "bad id", "text/plain")
            if (REVIEW_STATUS.get(stem) or {}).get("state") == "posting":
                return self.send(409, "This clip is being posted right now", "text/plain")
            if self.path == "/api/review/post":
                if not (REVIEW / (stem + ".mp4")).exists():
                    return self.send(404, "clip not found", "text/plain")
                threading.Thread(target=review_post, args=(stem, vis), daemon=True).start()
            else:
                (REVIEW / (stem + ".json")).unlink(missing_ok=True)  # the info file first: the clip leaves the list at once, even if the video is still locked
                for f in [REVIEW / (stem.replace("clip_", "meta_") + ".json")] + [g for g in REVIEW.glob(stem + ".*")]:
                    review_unlink(f)  # a locked video is retried now and purged later (review_purge) if it still cannot go
                REVIEW_STATUS.pop(stem, None)
        elif self.path == "/api/config":
            try:
                c = clean(json.loads(self.rfile.read(n)), read_cfg())
                if blocked_msg(c) and c.get("autopilot"):
                    c["autopilot"] = False  # saving an empty list with trending off switches the bot off
                save_cfg(c)
            except Exception as e:
                return self.send(400, f"invalid: {e}", "text/plain")
        else:
            return self.send(404, "no", "text/plain")
        self.send(200, "{}")

    def log_message(self, *a): pass


def ytdlp_loop():
    """yt-dlp daily update check while the dashboard runs (every run also checks before it starts; both share the 24 h / 6 h stamp in ytdlp_update.json)."""
    time.sleep(90)
    while True:
        try:
            subprocess.run([BGPY, "scripts/update_ytdlp.py"], cwd=R, creationflags=NOWIN, timeout=1200)
        except Exception:
            pass
        time.sleep(3 * 3600)


if __name__ == "__main__":
    try:
        secretstore.migrate_all()  # any old plain-text key becomes an encrypted file
    except Exception:
        pass
    threading.Thread(target=ytdlp_loop, daemon=True).start()
    threading.Thread(target=autopilot_loop, daemon=True).start()
    threading.Thread(target=live_loop, daemon=True).start()
    threading.Thread(target=stats_loop, daemon=True).start()
    print("Clippy dashboard on http://127.0.0.1:8765  (Ctrl+C to stop)")
    http.server.ThreadingHTTPServer(("127.0.0.1", 8765), H).serve_forever()
