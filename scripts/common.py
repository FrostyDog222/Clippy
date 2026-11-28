"""Shared helpers: paths, tool lookup, yt-dlp and ffmpeg wrappers."""
import hashlib, json, os, re, shutil, subprocess, sys, time
from pathlib import Path

ROOT = Path(os.environ.get("CLIP_DESK_HOME", Path.cwd()))
JOBS = ROOT / "jobs"


BROWSERS = (  # (name, yt-dlp name, install places): Google Chrome first, Microsoft Edge when it is not installed (every Windows 10 / 11 PC has Edge, same engine)
    ("Google Chrome", "chrome", (r"C:\Program Files\Google\Chrome\Application\chrome.exe", r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                                 os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"))),
    ("Microsoft Edge", "edge", (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe")),
)


def find_browser():
    """(name, path, yt-dlp name) of the browser Clippy uses for sign-in and uploads, or (None, None, None). Chrome is preferred; users do not need it, Edge does the same job."""
    for name, yt, paths in BROWSERS:
        p = next((x for x in paths if Path(x).exists()), None)
        if p:
            return name, p, yt
    return None, None, None


def whisper_types(device):
    """The compute types to try for faster-whisper on this device, best first. A GPU decides what it can do (older graphics cards have no float16, which made the
    speech recognition fail with "Requested float16 compute type"), so the list comes from what the device itself reports."""
    if device != "cuda":
        return ["int8", "float32"]
    try:
        import ctranslate2
        sup = set(ctranslate2.get_supported_compute_types("cuda"))
    except Exception:
        sup = {"float16"}
    return [t for t in ("float16", "int8_float16", "int8", "float32") if t in sup] or ["int8"]


def load_whisper(device, size="small"):
    """A faster-whisper model on "cuda" or "cpu" with the first compute type that device supports. Raises the last error when none works (callers fall back to the CPU)."""
    from faster_whisper import WhisperModel
    last = None
    for ct in whisper_types(device):
        try:
            return WhisperModel(size, device=device, compute_type=ct)
        except (RuntimeError, ValueError) as e:
            last = e
            print(f"Whisper on {device} with {ct} is not available here ({str(e)[:80]})", file=sys.stderr)
    raise last or RuntimeError("no compute type works")


def pid_alive(pid):
    """Is a process with this id still running? (Windows; True when it cannot be told)"""
    try:
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x100000, False, int(pid))  # SYNCHRONIZE
        if not h:
            return False
        try:
            return ctypes.windll.kernel32.WaitForSingleObject(h, 0) == 0x102
        finally:
            ctypes.windll.kernel32.CloseHandle(h)
    except Exception:
        return True


def lock_is_stale(lf, max_age=3600):
    """A post.lock whose owner is gone (crash, power loss): the lock holds its owner's process id, so it is stale as soon as that process does not exist. An old lock without an id
    (written by an older version) is stale after an hour."""
    try:
        age = time.time() - Path(lf).stat().st_mtime
    except OSError:
        return False
    try:
        pid = int(Path(lf).read_text().strip() or 0)
    except (OSError, ValueError):
        pid = 0
    return (not pid_alive(pid)) if pid else age > max_age


def tool(name):
    p = shutil.which(name)
    if not p:
        sys.exit(f"{name} not found. Run the setup skill first (it installs ffmpeg and yt-dlp).")
    return p


def run(cmd, check=True, capture=False):
    cook = "--cookies-from-browser" in list(map(str, cmd))  # then stderr is read here, to notice a cookie problem and retry without cookies
    kw = dict(check=False, text=True, encoding="utf-8", errors="replace", stdout=subprocess.PIPE if capture else None, stderr=subprocess.PIPE if (capture or cook) else None)
    r = subprocess.run(cmd, **kw)
    if cook and r.returncode != 0 and cookie_failed(r.stderr):
        note_cookie_failure()
        print("yt-dlp could not read the browser cookies: trying again without them", file=sys.stderr)
        r = subprocess.run(without_cookies(cmd), **kw)
    if cook and not capture and r.stderr:
        sys.stderr.write(r.stderr)  # what the user would have seen anyway
    if check and r.returncode != 0:
        msg = (r.stderr or "")[-1500:] if capture else ""
        sys.exit(f"command failed ({r.returncode}): {' '.join(map(str, cmd))[:200]}\n{msg}")
    return r


COOKIE_BAD = Path(__file__).resolve().parent.parent / "yt_cookies_bad.json"  # written when yt-dlp could not read the browser cookies (newer Chrome encrypts them): no cookies for a few hours
COOKIE_ERR = re.compile(r"cookie database|failed to decrypt|dpapi|could not (find|copy).{0,40}cookie|cookies? (file|jar)|cookies-from-browser|app.?bound", re.I)


def cookie_failed(text):
    """Did yt-dlp fail because it could not read the browser's cookies? (Then the same command works without them.)"""
    return bool(COOKIE_ERR.search(text or ""))


def note_cookie_failure(hours=6):
    try:
        COOKIE_BAD.write_text(json.dumps({"until": time.time() + hours * 3600}), encoding="utf-8")
    except OSError:
        pass


def without_cookies(cmd):
    """The same yt-dlp command minus its --cookies-from-browser <value> pair."""
    out, skip = [], False
    for x in map(str, cmd):
        if skip:
            skip = False
        elif x == "--cookies-from-browser":
            skip = True
        else:
            out.append(x)
    return out


def youtube_cookie_args():
    """Config "youtube_cookies": true -> yt-dlp reads the cookies of Clippy's own dedicated Chrome profile (chrome_profile, the one signed in for uploads),
    so YouTube stops asking "confirm you are not a bot". Only YouTube requests use them; nothing is copied or stored elsewhere."""
    try:
        here = Path(__file__).resolve().parent.parent
        cfg = json.loads((here / "autopost_config.json").read_text(encoding="utf-8"))
        prof = here / "chrome_profile"
        try:
            if json.loads(COOKIE_BAD.read_text(encoding="utf-8"))["until"] > time.time():  # the cookies could not be read a moment ago: go without them for now
                return []
        except Exception:
            pass
        if (here / "post.lock").exists() and not lock_is_stale(here / "post.lock"):  # an upload is using that Chrome profile right now: its cookie file is locked and cannot be copied (the run would fail), so go without cookies this once
            return []
        if cfg.get("youtube_cookies") and prof.exists():
            return ["--cookies-from-browser", f"{find_browser()[2] or 'chrome'}:{prof}"]
    except Exception:
        pass
    return []


def ytdlp_base(url=None):
    import importlib.util
    exe = [sys.executable, "-m", "yt_dlp"] if importlib.util.find_spec("yt_dlp") else [tool("yt-dlp")]
    base = exe + ["--no-warnings", "--no-progress"]
    if sys.platform == "win32":
        base += ["--compat-options", "no-certifi"]  # use the Windows certificate store
    return base + (youtube_cookie_args() if (url is None or re.search(r"youtube\.com|youtu\.be", str(url))) else [])  # the cookies only ever go to YouTube links (never to Twitch or anything else)


def ytdlp_json(url, extra=()):
    r = run(ytdlp_base(url) + ["-J", *extra, url], capture=True)
    return json.loads(r.stdout)


def is_url(s):
    return bool(re.match(r"https?://", s))


def job_dir(src):
    key = hashlib.sha1(src.encode()).hexdigest()[:8]
    stem = re.sub(r"[^a-z0-9]+", "-", Path(src).stem.lower() if not is_url(src) else src.split("v=")[-1].split("/")[-1].lower())[:24].strip("-")
    d = JOBS / f"{stem or 'job'}-{key}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def ffprobe(path):
    r = run([tool("ffprobe"), "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height,r_frame_rate",
             "-of", "json", str(path)], capture=True)
    return json.loads(r.stdout)


def save_json(path, obj):
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))
