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
        hist = [h["secs"] / max(1, h.get("clips", 1)) for h in json.loads((R / "run_history.json").read_text(encoding="utf-8"))[-8:] if h.get("secs", 0) >= 90 and h.get("result") == "0"]  # seconds PER CLIP  # only runs that finished normally count: a crashed or stopped run says nothing about how long a run takes
    except Exception:
        hist = []
    typical = (sum(hist) / len(hist)) * max(1, int(d.get("want", 1))) if hist else None  # a typical clip times the clips this run wants
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


def run_now(force=False, clips=1):
    """Clippy's own pipeline (scripts/autorun.py, no Claude). force=True is the manual "Run now" button (ignores the daily limit / window / gap);
    the autopilot calls it without force so tick.py decides. (This used to start run_slot.bat, the old Claude-driven mode.)"""
    set_prog(1, "Starting")
    subprocess.Popen([BGPY, "scripts/autorun.py", *(["--force"] if force else []), "--clips", str(max(1, min(10, int(clips))))], cwd=R, creationflags=NOWIN | DETACHED,
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
PORT = int(os.environ.get("CLIPPY_PORT", "8765"))  # (only tests use another port)
BOOT = str(int(time.time() * 1000))  # identifies THIS running dashboard: the page reloads itself when it sees a different one
class OneServer(http.server.ThreadingHTTPServer):
    """Only ONE dashboard per port: by default Python lets a second copy share the port on Windows, which ran two schedulers at once (a clip could post twice).
    Without port sharing a second copy cannot start ('the dashboard port is in use') and ends."""
    allow_reuse_address = False


SERVER = None


def code_sig():
    """A fingerprint of the program files the running dashboard loaded at start (dashboard.py, tick.py, scripts/*.py): when it differs from the one taken at start, the files changed
    (an update, or a new version) and the dashboard should be restarted to use them."""
    try:
        files = [R / "dashboard.py", R / "tick.py", *sorted((R / "scripts").glob("*.py"))]
        return "|".join(f"{f.name}:{int(f.stat().st_mtime)}:{f.stat().st_size}" for f in files if f.exists())
    except Exception:
        return ""


START_SIG = code_sig()


def restart_wanted():
    return code_sig() != START_SIG


def restart_self():
    """Hard restart: a tiny helper process (no window, survives this one) waits until this dashboard has let go of the port, then starts a fresh dashboard; then this one exits.
    Runs that are already going (their own processes) and the saved settings are not touched."""
    lines = ["import socket, subprocess, time",
             "time.sleep(1.0)",
             "for _ in range(60):",
             "    s = socket.socket()",
             "    try:",
             f"        s.bind(('127.0.0.1', {PORT})); s.close(); break",
             "    except OSError:",
             "        s.close(); time.sleep(0.5)",
             f"subprocess.Popen([{BGPY!r}, 'dashboard.py'], cwd={str(R)!r}, creationflags=0x08000000 | 0x00000008, close_fds=True)"]
    helper = chr(10).join(lines)
    subprocess.Popen([BGPY, "-c", helper], cwd=R, creationflags=NOWIN | DETACHED, close_fds=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.5)
    try:
        if SERVER:
            SERVER.shutdown()
    finally:
        os._exit(0)


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


MAX_WAITING = 8  # prepare-only mode: stop preparing new clips while this many wait in "Ready to post"


def waiting_clips():
    """Clips in Ready to post that were not posted yet: (stem, creation time)."""
    out = []
    for f in (REVIEW.glob("*__clip_*.json") if REVIEW.exists() else []):
        if f.name.endswith(".posted.json") or not f.with_suffix(".mp4").exists() or not STEM.match(f.stem):
            continue
        sf = REVIEW / (f.stem + ".posted.json")
        try:
            if sf.exists() and any(json.loads(sf.read_text(encoding="utf-8")).values()):
                continue
            out.append((f.stem, dt.datetime.fromtimestamp(int(f.stem.split("__")[0]))))
        except Exception:
            continue
    return out


def sched_posts(c):
    """What the schedule counts. Auto-Post ON: the real posts. Auto-Post OFF ('prepare only'): a prepared clip counts like a post (daily limit, minimum gap,
    time window), so Autopilot prepares about as many clips as it would have posted instead of running non-stop."""
    posts = read_posts()
    if c.get("auto_post"):
        return posts
    return posts + [{"date": t.strftime("%Y-%m-%d"), "ts": t.isoformat(timespec="seconds")} for _, t in waiting_clips()]


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
            if c.get("auto_post"):
                ok, _, _ = tick.decide(c, read_posts(), dt.datetime.now(), PAUSE.exists())
                if ok:
                    run_now()
            else:  # prepare only: find and prepare clips on the same schedule, never post, and stop while enough are waiting
                if len(waiting_clips()) >= MAX_WAITING:
                    continue
                ok, _, _ = tick.decide(c, sched_posts(c), dt.datetime.now(), PAUSE.exists())
                if ok:
                    today = dt.date.today().isoformat()
                    left = int(c.get("posts_per_day", 1)) - len([p for p in sched_posts(c) if p.get("date") == today and not p.get("deleted")])
                    run_now(force=True, clips=max(1, min(int(c.get("auto_clips_per_run", 1) or 1), left)))  # several clips per automatic run, never past the day's quota  # our own check above already applied the schedule (autorun would only look at real posts)
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
    out["time_format"] = new.get("time_format") if new.get("time_format") in ("12", "24") else "12"  # 12-hour (US) or 24-hour (Europe) times and dates
    out["min_score"] = round(max(1.0, min(10.0, float(new.get("min_score", 7)))) * 2) / 2  # the lowest score a clip may have to be added (default 7, steps of 0.5)
    out["display_name"] = re.sub(r"\s+", " ", str(new.get("display_name", ""))).strip()[:40]  # the name in the welcome line
    out["auto_clips_per_run"] = max(1, min(10, int(new.get("auto_clips_per_run", 1) or 1)))  # clips per automatic (Autopilot) run; the manual "clips this run" is saved right away by /api/clips
    out["blocked_streamers"] = ", ".join(x.strip() for x in re.split(r"[,;\n]", str(new.get("blocked_streamers", ""))) if x.strip())[:300]  # streamers that are never clipped
    out["auto_update"] = bool(new.get("auto_update", True))  # install new Clippy versions by itself (settings and clips are never touched)
    # auto_post is not set here: its switches (Ready to post and Settings) save immediately through /api/autopost; OFF (default) = finished clips wait in "Ready to post"
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


WHY = ("boring", "nudity", "crop", "repeat", "other")  # why a clip was a 👎


def review_remove(stem):
    """Take a clip out of Ready to post (its video and files are deleted; this cannot be undone)."""
    try:  # removing a clip nobody rated and nobody posted = "meh": keep that as a learning example (the clip's signals are deleted with it)
        cf, pf = REVIEW / (stem + ".json"), REVIEW / (stem + ".posted.json")
        d = json.loads(cf.read_text(encoding="utf-8")) if cf.exists() else {}
        posted = pf.exists() and any(json.loads(pf.read_text(encoding="utf-8")).values())
        if d.get("features") and not d.get("rating") and not posted:
            log_rating(stem, d, 0, removed=True); learn_refit()
    except Exception:
        pass
    (REVIEW / (stem + ".json")).unlink(missing_ok=True)  # the info file first: the clip leaves the list at once, even if the video is still locked
    for f in [REVIEW / (stem.replace("clip_", "meta_") + ".json")] + [g for g in REVIEW.glob(stem + ".*")]:
        review_unlink(f)  # a locked video is retried now and purged later (review_purge) if it still cannot go
    REVIEW_STATUS.pop(stem, None)


def clip_words(d):
    """The words spoken in a clip, from the transcript of the job it was cut from (found by the source video id and the clip's time range)."""
    sid, a, b = d.get("source_id"), float(d.get("start") or 0), float(d.get("end") or 0)
    for job in sorted((R / "jobs").glob(f"{sid}-*")) if sid else []:
        try:
            words = json.loads((job / "transcript.json").read_text(encoding="utf-8"))
        except Exception:
            continue
        txt = " ".join(w["w"] for w in words if a <= w["s"] < b)
        if txt.strip():
            return txt
    return ""


LAYOUTS = ("blur", "facecam", "fill")


def review_recut(stem, layout):
    """Cut this clip again from its source with another crop (blurred background / webcam on top / fill the screen): same moment, same texts, same rating. The source video
    is downloaded again when it was cleaned up. Runs in the background (REVIEW_STATUS shows it); the clip stays as it was when anything fails."""
    def say(state, msg):
        REVIEW_STATUS[stem] = {"state": state, "msg": msg}
    try:
        from common import job_dir, ytdlp_base, run
        cj = REVIEW / (stem + ".json"); d = json.loads(cj.read_text(encoding="utf-8"))
        url, pick = d["source_url"], int(stem.split("clip_")[1])
        job = job_dir(url); src = job / "source.mp4"
        if not src.exists():
            say("posting", "Re-cutting this clip: downloading the source again...")
            run(ytdlp_base(url) + ["-f", "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080]/b", "--merge-output-format", "mp4", "-o", str(src), url])
        say("posting", f"Re-cutting this clip ({layout})...")
        caps = ["--captions", "none"] if (d.get("no_caps") or d.get("recut")) else []
        r = subprocess.run([str(R / ".venv" / "Scripts" / "python.exe"), "scripts/cut.py", str(job), "--pick", str(pick), "--layout", layout, "--hook", d.get("hook", ""),
                            "--start", str(d["start"]), "--end", str(d["end"]), *caps], cwd=R, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800,
                           env={**os.environ, "PYTHONUTF8": "1", "CLIP_DESK_HOME": str(R)}, creationflags=0x08000000)
        new = job / "edits" / f"clip_{pick}.mp4"
        if r.returncode != 0 or not new.exists():
            raise RuntimeError("The cut failed" + (" (no webcam found in this video?)" if layout == "facecam" else "") + ": " + (r.stderr or r.stdout or "")[-160:])
        nj = json.loads((job / "edits" / f"clip_{pick}.json").read_text(encoding="utf-8"))
        if nj.get("layout") and nj["layout"] != layout:  # cut.py fell back to another layout (no usable webcam): do not swap in a video the user did not ask for
            raise RuntimeError(f"This video has no usable webcam, so the '{layout}' crop is not possible (it would have been '{nj['layout']}').")
        for _ in range(10):  # the page may still be holding the old video for a moment
            try:
                shutil.copy2(new, REVIEW / (stem + ".mp4")); break
            except PermissionError:
                time.sleep(1)
        else:
            raise RuntimeError("Could not replace the video (is it playing in another window?)")
        for k in ("trim", "orig_seconds"):
            d.pop(k, None)
        d["layout"] = layout; d["seconds"] = round(float(nj.get("end", d["end"])) - float(nj.get("start", d["start"])), 1)
        cj.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        (REVIEW / (stem + ".orig")).unlink(missing_ok=True)
        review_unpost(stem)  # a re-cut is a new video: it can be posted again (delete the old online one first)
        REVIEW_STATUS.pop(stem, None)
    except Exception as e:
        say("error", "Re-cut failed, the clip is unchanged: " + str(e)[:220])


def review_rejoke(stem):
    """Ask the AI title writer for a different one-line joke for this clip (the same writer and the same data as a normal run: the clip's words + the source title) and
    put it in the title and the first description line. Returns the new line. Raises ValueError with a plain message."""
    cj = REVIEW / (stem + ".json")
    d = json.loads(cj.read_text(encoding="utf-8"))
    mf = REVIEW / (stem.replace("clip_", "meta_") + ".json")
    meta = json.loads(mf.read_text(encoding="utf-8")) if mf.exists() else {}
    cfg = read_cfg()
    if cfg.get("title_writer") not in ("claude", "api"):
        raise ValueError("A new joke needs the AI title writer: choose one in Settings (Claude or your own API key).")
    text = clip_words(d)
    if not text:
        raise ValueError("The words of this clip are no longer on this PC, so no new joke can be written. Use Edit text instead.")
    ai = ai_title.write(cfg, text, d.get("source_channel") or "", d.get("source_title") or "")
    if not ai:
        raise ValueError("The AI title writer did not answer (key, sign-in or limit). Try again in a moment.")
    line = ai["tiktok_line"][:85].strip()
    et, ed, eg = split_meta(meta)
    rest = [x for x in ed.splitlines()]
    rest = rest[1:] if rest and not rest[0].startswith("🎮") else rest
    review_edit(stem, line, chr(10).join([line] + rest), eg, meta.get("tiktok_custom", ""))
    return line


def log_rating(stem, d, rating, removed=False):
    """One line in ratings.jsonl (local only): the clip's rating (+1 stood out good, -1 stood out bad, 0 meh) with the signals behind it. The log outlives the clip."""
    with open(R / "ratings.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"id": stem, "rating": rating, "ts": dt.datetime.now().isoformat(timespec="seconds"), "rank": d.get("rank"), "features": d.get("features"),
                             "seconds": d.get("seconds"), "creator": d.get("source_channel") or d.get("creator"), **({"reason": d["reason"]} if d.get("reason") else {}), **({"removed": True} if removed else {})}, ensure_ascii=False) + chr(10))


_LEARN = {"t": 0.0, "v": None}


def learn_refit():
    """Learn from the ratings again, in the background (right after a 👍 / 👎 / removal), so the next run already uses it."""
    def go():
        try:
            import learn
            learn.fit(); _LEARN["t"] = 0.0
        except Exception:
            pass
    threading.Thread(target=go, daemon=True).start()


def learn_info():
    """What Clippy learned (plain words), cached for 20 s: the dashboard asks every few seconds."""
    if _LEARN["v"] is None or time.time() - _LEARN["t"] > 20:
        try:
            import learn
            _LEARN["v"] = learn.summary()
        except Exception:
            _LEARN["v"] = None
        _LEARN["t"] = time.time()
    return _LEARN["v"]


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
            if (REVIEW_STATUS.get(f.stem) or {}).get("state") == "done" and not sf.exists():  # marked as not posted (or re-cut): the old "Posted." line must go
                REVIEW_STATUS.pop(f.stem, None)
            et, ed, eg = split_meta(meta)
            out.append({"id": f.stem, "title": meta.get("yt_title", ""), "desc": meta.get("yt_description", ""), "caption": meta.get("tiktok_caption", ""),
                        "edit_title": et, "edit_desc": ed, "edit_tags": " ".join(eg), "edit_tiktok": meta.get("tiktok_custom", ""), "edited": bool(meta.get("edited")),
                        "seconds": d.get("seconds"), "layout": d.get("layout"), "creator": d.get("source_channel"), "source": d.get("source_title"), "rating": d.get("rating", 0), "reason": d.get("reason"), "scheduled": d.get("scheduled"), "sched_missed": d.get("schedule_missed"), "trim": d.get("trim"), "orig_seconds": d.get("orig_seconds") or d.get("seconds"), "rank": d.get("rank"), "score": d.get("score"), "features": d.get("features"),
                        "posted": json.loads(sf.read_text(encoding="utf-8")) if sf.exists() else {}, "status": REVIEW_STATUS.get(f.stem)})
        except Exception:
            continue
    return out


def _ffbin(name):
    import shutil
    p = shutil.which(name)
    if not p:
        raise RuntimeError(f"{name} was not found")
    return p


def _duration(f):
    r = subprocess.run([_ffbin("ffprobe"), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(f)], capture_output=True, text=True, creationflags=NOWIN, timeout=60)
    return float(r.stdout.strip())


def _replace(src, dst, tries=8):
    """os.replace that waits for a video the page may still be playing (Windows locks it for a moment)."""
    for _ in range(tries):
        try:
            os.replace(src, dst); return
        except PermissionError:
            time.sleep(0.5)
    raise RuntimeError("the video is in use: pause it and try again")


def review_trim(stem, start, end, fade=True):
    """Cut a finished clip to [start, end] seconds of its ORIGINAL (kept beside it as <stem>.orig, so a second trim or Reset starts from the full clip). The clip's captions are
    burned in, so they stay in sync. The result replaces <stem>.mp4: that is the file Post uploads (with the saved title / description / hashtags)."""
    mp4, orig, cj = REVIEW / (stem + ".mp4"), REVIEW / (stem + ".orig"), REVIEW / (stem + ".json")
    if not mp4.exists() or not cj.exists():
        raise ValueError("clip not found")
    if not orig.exists():
        import shutil
        shutil.copy2(mp4, orig)
    dur = _duration(orig)
    start, end = float(start), float(end)
    if not (0 <= start < end <= dur + 0.05) or end - start < 1.0:
        raise ValueError(f"choose a start and end inside the clip (it is {dur:.1f} s long, at least 1 s must remain)")
    end = min(end, dur); n = end - start
    tmp = REVIEW / (stem + ".trim.tmp")
    vf = ["-vf", f"fade=t=out:st={max(0.0, n - 0.3):.2f}:d=0.3", "-af", f"afade=t=out:st={max(0.0, n - 0.35):.2f}:d=0.35"] if fade and n > 2 else []
    cmd = [_ffbin("ffmpeg"), "-y", "-v", "error", "-ss", f"{start:.2f}", "-to", f"{end:.2f}", "-i", str(orig), *vf, "-c:v", "libx264", "-preset", "medium", "-crf", "18",
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", "-f", "mp4", str(tmp)]
    r = subprocess.run(cmd, capture_output=True, text=True, creationflags=NOWIN, timeout=300)
    if r.returncode != 0 or not tmp.exists() or tmp.stat().st_size < 1000:
        tmp.unlink(missing_ok=True); raise RuntimeError("cutting failed: " + (r.stderr or "")[-150:])
    _replace(tmp, mp4)
    d = json.loads(cj.read_text(encoding="utf-8"))
    d.setdefault("orig_seconds", d.get("seconds"))
    d["seconds"] = round(n, 1); d["trim"] = {"start": round(start, 2), "end": round(end, 2), "fade": bool(fade)}
    cj.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"seconds": d["seconds"]}


def review_trim_reset(stem):
    mp4, orig, cj = REVIEW / (stem + ".mp4"), REVIEW / (stem + ".orig"), REVIEW / (stem + ".json")
    if not orig.exists():
        return
    _replace(orig, mp4)
    d = json.loads(cj.read_text(encoding="utf-8"))
    if d.get("orig_seconds") is not None:
        d["seconds"] = d["orig_seconds"]
    d.pop("trim", None); d.pop("orig_seconds", None)
    cj.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


SCHED_GRACE = 15  # minutes: a schedule that is overdue by more than this (Clippy was closed / the PC asleep) is NOT posted by itself any more


def review_schedule(stem, at, mode):
    """Put a time on a clip in Ready to post: the scheduler below posts it then (with the chosen visibility)."""
    cj = REVIEW / (stem + ".json")
    if not cj.exists():
        raise ValueError("clip not found")
    when = dt.datetime.fromisoformat(str(at)[:16])
    if when <= dt.datetime.now() + dt.timedelta(minutes=1):
        raise ValueError("choose a time in the future")
    if mode not in ("private", "public"):
        raise ValueError("bad visibility")
    d = json.loads(cj.read_text(encoding="utf-8"))
    d["scheduled"] = {"at": when.isoformat(timespec="minutes"), "mode": mode}; d.pop("schedule_missed", None)
    cj.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


def plan_slots(now, n, per_day, start, end, gap, taken):
    """n posting times from `now` on: each day from `start` to `end` (HH:MM), at least `gap` minutes apart, at most `per_day` posts a day counting the times already in
    `taken` (scheduled or posted). Never in the past. Looks 21 days ahead."""
    sh, sm = map(int, start.split(":")); eh, em = map(int, end.split(":"))
    out = []
    for k in range(21):
        day = (now + dt.timedelta(days=k)).date()
        t, close = dt.datetime.combine(day, dt.time(sh, sm)), dt.datetime.combine(day, dt.time(eh, em))
        mine = sorted(x for x in taken if x.date() == day)
        count = len(mine)
        while t <= close and len(out) < n and count < per_day:
            if t > now + dt.timedelta(minutes=2) and all(abs((t - x).total_seconds()) >= gap * 60 for x in mine + [o for o in out if o.date() == day]):
                out.append(t); count += 1
            t += dt.timedelta(minutes=15)
        if len(out) >= n:
            break
    return out


def review_autoschedule(mode, now=None):
    """Give every waiting clip you liked (👍; not posted, not scheduled), best rank first, a time that respects the daily limit and the posting window from Settings. Returns how many."""
    if mode not in ("private", "public"):
        raise ValueError("bad visibility")
    now, c = now or dt.datetime.now(), read_cfg()
    taken, todo = [], []
    for f in REVIEW.glob("*__clip_*.json"):
        if f.name.endswith(".posted.json") or not f.with_suffix(".mp4").exists():
            continue
        d = json.loads(f.read_text(encoding="utf-8"))
        if (REVIEW_STATUS.get(f.stem) or {}).get("state") == "posting":
            continue
        pf = f.with_name(f.stem + ".posted.json")
        if pf.exists():
            taken.append(dt.datetime.fromtimestamp(pf.stat().st_mtime))
        elif d.get("scheduled"):
            taken.append(dt.datetime.fromisoformat(d["scheduled"]["at"]))
        elif d.get("rating") == 1:  # only the clips you liked (👍) are spread out; the rest stay for you to look at
            todo.append((-(float(d.get("rank") or 0)), f.stem))
    todo.sort()
    slots = plan_slots(now, len(todo), int(c.get("posts_per_day", 6)), c.get("window_start", "09:00"), c.get("window_end", "22:30"), int(c.get("min_gap_minutes", 120)), taken)
    for (_, stem), when in zip(todo, slots):
        review_schedule(stem, when.isoformat(), mode)
    return len(slots)


def review_unschedule(stem):
    cj = REVIEW / (stem + ".json")
    if cj.exists():
        d = json.loads(cj.read_text(encoding="utf-8")); d.pop("scheduled", None); d.pop("schedule_missed", None)
        cj.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


def review_unpost(stem):
    """'Mark as not posted': the clip gets its Post button back (the video that is already online is NOT touched: if it is not deleted there, posting again uploads it twice)."""
    (REVIEW / (stem + ".posted.json")).unlink(missing_ok=True)
    REVIEW_STATUS.pop(stem, None)


def sched_tick(now=None):
    """Post the clips whose scheduled time has come. A time that was missed by more than SCHED_GRACE minutes is dropped (and shown as missed), never posted late by surprise."""
    now = now or dt.datetime.now(); started = []
    for f in (sorted(REVIEW.glob("*__clip_*.json")) if REVIEW.exists() else []):
        if f.name.endswith(".posted.json") or not STEM.match(f.stem):
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8")); sc = d.get("scheduled")
            if not sc:
                continue
            at = dt.datetime.fromisoformat(sc["at"])
            if at > now:
                continue
            if (REVIEW_STATUS.get(f.stem) or {}).get("state") == "posting":
                continue
            d.pop("scheduled", None)
            if (now - at) > dt.timedelta(minutes=SCHED_GRACE):
                d["schedule_missed"] = sc["at"]
            else:
                d["scheduled_run"] = now.isoformat(timespec="seconds"); started.append((f.stem, sc.get("mode", "private")))
            f.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception:
            continue
    for stem, mode in started:
        threading.Thread(target=review_post, args=(stem, mode), daemon=True).start()
    return [x[0] for x in started]


def views_loop():
    """About once an hour: read the public view counts of clips that were posted 2+ days ago (scripts/views.py), then learn from them."""
    while True:
        time.sleep(120)
        try:
            import views
            if views.refresh():
                learn_refit()
        except Exception:
            pass
        time.sleep(3500)


def sched_loop():
    while True:
        time.sleep(30)
        try:
            sched_tick()
        except Exception:
            pass


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

    def serve_review(self, stem, orig=False):
        f = REVIEW / (stem + ".orig") if orig and (REVIEW / (stem + ".orig")).exists() else REVIEW / (stem + ".mp4")  # the untrimmed original for the trim preview (the clip itself when it was never trimmed)
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
        m = re.match(r"^/review/(\d+__clip_[A-Za-z0-9_-]+)\.(mp4|orig)$", self.path.split("?")[0])
        if m and self.headers.get("Host", "").split(":")[0] in ("127.0.0.1", "localhost"):
            return self.serve_review(m.group(1), m.group(2) == "orig")
        if self.path == "/api/state":
            c, posts = read_cfg(), read_posts()
            today = dt.date.today().isoformat()
            ok, why, mode = tick.decide(c, sched_posts(c), dt.datetime.now(), PAUSE.exists())
            log = LOG.read_text(encoding="utf-8")[-1500:] if LOG.exists() else ""
            return self.send(200, json.dumps({"config": c, "paused": PAUSE.exists(), "checks": checks(),
                                              "today": [p for p in posts if p.get("date") == today and not p.get("deleted")], "log": log,
                                              "progress": progress(), "signin": signin_state(), "jobs_mb": cleanup.jobs_mb(), "all_languages": textutil.LANGS, "perf": perf(), "ai_keys": {p["id"]: secretstore.exists(f"key_{p['id']}") for p in c.get("ai_profiles", [])}, "blocked": blocked_msg(c), "live": live_status(),
                                              "review": review_items(), "run_stale": run_stale(), "run_timing": run_timing(), "run_suspended": run_suspended(),
                                              "update": update_info(), "learn": learn_info(), "boot": BOOT, "restart_needed": restart_wanted(), "html": int((R / "dashboard.html").stat().st_mtime),
                                              "runlog": (R / "autorun.log").read_text(encoding="utf-8", errors="replace")[-8000:] if (R / "autorun.log").exists() else "",
                                              "why": "Next run is due now" if ok else "Waiting: " + why,
                                              "mode": tick.decide({**c, "posts_per_day": 99, "window_start": "00:00", "window_end": "23:59", "min_gap_minutes": 0}, [], dt.datetime.now())[2]}))
        self.send(200, (R / "dashboard.html").read_text(encoding="utf-8"), "text/html")

    def do_POST(self):
        if self.headers.get("Host", "").split(":")[0] not in ("127.0.0.1", "localhost"):  # DNS-rebinding guard
            return self.send(403, "bad host", "text/plain")
        org = self.headers.get("Origin")  # cross-site request guard: a web page you happen to have open must not be able to POST to this local dashboard
        if org and org not in (f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"):
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
        elif self.path in ("/api/update/check", "/api/update/apply"):
            if self.path.endswith("apply") and run_alive():
                return self.send(409, "A run or post is in progress: update when it has finished", "text/plain")
            update_run(self.path.rsplit("/", 1)[1], wait=True)
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
        elif self.path == "/api/learn/reset":  # start fresh: the learned model goes, the ratings log is set aside (renamed)
            import learn
            learn.reset(); _LEARN["v"] = None
        elif self.path == "/api/clips":  # "how many clips should this run make" (manual runs), saved right away
            try:
                n = int(json.loads(self.rfile.read(n))["n"]); c = read_cfg(); c["clips_per_run"] = max(1, min(10, n)); save_cfg(c)
            except Exception:
                return self.send(400, "bad number", "text/plain")
        elif self.path == "/api/autopost":  # Auto-Post on/off right away (ON: finished clips are posted by Clippy; OFF: they wait in Ready to post)
            c = read_cfg(); c["auto_post"] = not c.get("auto_post", False); save_cfg(c)
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
            run_now(True, int(read_cfg().get("clips_per_run", 1) or 1))  # the manual button: run now even outside the schedule, with the "clips this run" number
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
        elif self.path == "/api/restart":  # hard restart of the dashboard (the new code is loaded); refused while an upload is running
            if run_alive() and ((R / "post.lock").exists() or any((x or {}).get("state") == "posting" for x in REVIEW_STATUS.values())):
                return self.send(409, "An upload is running: restart when it has finished", "text/plain")
            self.send(200, "{}")
            threading.Thread(target=restart_self, daemon=True).start()
            return
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
        elif self.path == "/api/review/recut":
            try:
                body = json.loads(self.rfile.read(n)); stem = body["id"]; layout = body["layout"]; assert STEM.match(stem) and layout in LAYOUTS and (REVIEW / (stem + ".mp4")).exists()
            except Exception:
                return self.send(400, "bad values", "text/plain")
            if (REVIEW_STATUS.get(stem) or {}).get("state") == "posting" or (R / "post.lock").exists():
                return self.send(409, "Busy: wait until the current post or re-cut has finished", "text/plain")
            REVIEW_STATUS[stem] = {"state": "posting", "msg": "Re-cutting this clip..."}
            threading.Thread(target=review_recut, args=(stem, layout), daemon=True).start()
        elif self.path == "/api/review/rejoke":
            try:
                stem = json.loads(self.rfile.read(n))["id"]; assert STEM.match(stem) and (REVIEW / (stem + ".mp4")).exists()
            except Exception:
                return self.send(400, "bad id", "text/plain")
            if (REVIEW_STATUS.get(stem) or {}).get("state") == "posting":
                return self.send(409, "This clip is being posted right now", "text/plain")
            try:
                return self.send(200, json.dumps({"line": review_rejoke(stem)}))
            except ValueError as e:
                return self.send(400, str(e), "text/plain")
        elif self.path == "/api/review/autoschedule":
            try:
                k = review_autoschedule(json.loads(self.rfile.read(n)).get("mode", "private"))
            except (ValueError, KeyError, TypeError) as e:
                return self.send(400, str(e) or "bad values", "text/plain")
            return self.send(200, json.dumps({"scheduled": k}))
        elif self.path in ("/api/review/schedule", "/api/review/schedule/cancel", "/api/review/unpost"):
            try:
                body = json.loads(self.rfile.read(n)); stem = body["id"]; assert STEM.match(stem)
            except Exception:
                return self.send(400, "bad id", "text/plain")
            if (REVIEW_STATUS.get(stem) or {}).get("state") == "posting":
                return self.send(409, "This clip is being posted right now", "text/plain")
            try:
                if self.path.endswith("cancel"):
                    review_unschedule(stem)
                elif self.path.endswith("unpost"):
                    review_unpost(stem)
                else:
                    review_schedule(stem, body["at"], body.get("mode", "private"))
            except (ValueError, KeyError, TypeError) as e:
                return self.send(400, str(e) or "bad values", "text/plain")
        elif self.path in ("/api/review/trim", "/api/review/trim/reset"):  # cut a few seconds off a finished clip (or go back to the full clip)
            try:
                body = json.loads(self.rfile.read(n)); stem = body["id"]; assert STEM.match(stem)
            except Exception:
                return self.send(400, "bad id", "text/plain")
            if (REVIEW_STATUS.get(stem) or {}).get("state") == "posting":
                return self.send(409, "This clip is being posted right now: wait until it is done", "text/plain")
            try:
                if self.path.endswith("reset"):
                    review_trim_reset(stem)
                else:
                    review_trim(stem, body["start"], body["end"], bool(body.get("fade", True)))
            except (ValueError, KeyError, TypeError) as e:
                return self.send(400, str(e) or "bad values", "text/plain")
            except Exception as e:
                return self.send(500, str(e)[:200], "text/plain")
        elif self.path == "/api/review/rate":  # thumbs up / down on a clip: kept with the signals behind it (ratings.jsonl, local only) to learn what works
            try:
                body = json.loads(self.rfile.read(n)); stem = body["id"]; assert STEM.match(stem) and body["rating"] in (-1, 0, 1)
                cf = REVIEW / (stem + ".json"); d = json.loads(cf.read_text(encoding="utf-8")); d["rating"] = body["rating"]
                d.pop("reason", None)
                if body["rating"] == -1 and body.get("reason") in WHY:
                    d["reason"] = body["reason"]
                cf.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
                log_rating(stem, d, body["rating"]); learn_refit()
            except Exception:
                return self.send(400, "bad rating", "text/plain")
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
                review_remove(stem)
        elif self.path == "/api/review/remove_all":  # every clip in Ready to post (not the one being posted right now); cannot be undone
            for f in list(REVIEW.glob("*__clip_*.json")):
                if not f.name.endswith(".posted.json") and STEM.match(f.stem) and (REVIEW_STATUS.get(f.stem) or {}).get("state") != "posting":
                    review_remove(f.stem)
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


def update_run(what, wait=False):
    """Runs scripts/update_clippy.py (check | apply) hidden; it only talks to GitHub and only replaces program files."""
    try:
        subprocess.run([BGPY, "scripts/update_clippy.py", what], cwd=R, creationflags=NOWIN, timeout=180 if wait else 300)
    except Exception:
        pass


def update_info():
    try:
        st = json.loads((R / "update_state.json").read_text(encoding="utf-8"))
    except Exception:
        st = {}
    st["off"] = (R / ".no_update").exists(); st["auto"] = bool(read_cfg().get("auto_update", True))
    return st


def update_loop():
    """Every 6 hours (and shortly after start): look for a newer Clippy; with 'Update automatically' on, install it while nothing is running."""
    time.sleep(120)
    while True:
        try:
            if (R / ".no_update").exists():
                time.sleep(6 * 3600); continue
            update_run("check")
            if update_info().get("available") and read_cfg().get("auto_update", True) and not run_alive() and not running():
                update_run("apply")
        except Exception:
            pass
        time.sleep(6 * 3600)


def ytdlp_loop():
    """yt-dlp daily update check while the dashboard runs (every run also checks before it starts; both share the 24 h / 6 h stamp in ytdlp_update.json)."""
    time.sleep(90)
    while True:
        try:
            subprocess.run([BGPY, "scripts/update_ytdlp.py"], cwd=R, creationflags=NOWIN, timeout=1200)
        except Exception:
            pass
        time.sleep(3 * 3600)


_LOCK = []  # keeps the single-dashboard lock alive for the whole run


def single_dashboard():
    """Only ONE dashboard per user, whatever the port: a named Windows lock that the system releases by itself when the program ends (no stale lock file). A second
    dashboard waits a few seconds (a restart hands over from the old one to the new one) and then ends. Test runs (CLIPPY_NOBG / CLIPPY_ALLOW_MULTI) are exempt."""
    if os.environ.get("CLIPPY_NOBG") or os.environ.get("CLIPPY_ALLOW_MULTI") or os.name != "nt":
        return
    import ctypes
    k = ctypes.windll.kernel32
    for _ in range(40):
        h = k.CreateMutexW(None, False, "Local" + chr(92) + "ClippyDashboardSingleInstance")
        if h and k.GetLastError() != 183:  # 183 = ERROR_ALREADY_EXISTS: another dashboard owns it
            _LOCK.append(h); return
        if h:
            k.CloseHandle(h)
        time.sleep(0.5)
    sys.exit("Another Clippy dashboard is already open. Only one can run at a time: use that one (or press Quit dashboard there first).")


if __name__ == "__main__":
    single_dashboard()
    try:
        secretstore.migrate_all()  # any old plain-text key becomes an encrypted file
    except Exception:
        pass
    if not os.environ.get("CLIPPY_NOBG"):  # (tests start the server without the background loops)
        threading.Thread(target=ytdlp_loop, daemon=True).start()
        threading.Thread(target=update_loop, daemon=True).start()
        threading.Thread(target=autopilot_loop, daemon=True).start()
        threading.Thread(target=sched_loop, daemon=True).start()
        threading.Thread(target=views_loop, daemon=True).start()
        threading.Thread(target=live_loop, daemon=True).start()
        threading.Thread(target=stats_loop, daemon=True).start()
    print(f"Clippy dashboard on http://127.0.0.1:{PORT}  (Ctrl+C to stop)")
    for _ in range(40):  # right after a restart the old dashboard may still be letting go of the port
        try:
            SERVER = OneServer(("127.0.0.1", PORT), H); break
        except OSError:
            time.sleep(0.5)
    else:
        sys.exit("the dashboard port is in use")
    SERVER.serve_forever()
    os._exit(0)  # after /api/quit the whole program ends (no leftover process)
