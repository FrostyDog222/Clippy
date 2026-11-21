"""Clippy network guard: Python processes of this project may only talk to the places they need (YouTube, TikTok, and what you switched on).
Loaded automatically in every Python process of this project's venv by sitecustomize.py (see scripts/sitecustomize_clippy.py; setup.bat installs it).
Everything else is refused at the DNS/connect level and written to netguard.log. Loopback (127.0.0.1) is always allowed.

Allowed:   YouTube (+ its CDNs and the YouTube / Google / Gemini APIs), TikTok (+ its API), Twitch, and the official AI API address you entered;
           pypi.org only for the daily yt-dlp update check.
Everything else (analytics, telemetry, model hubs, random hosts) is blocked. Chrome (the uploads) is a separate program: see the flags in upload.py.
Config "network_guard": "enforce" | "log" (only record what would be blocked) | "off".   Env CLIPPY_NETGUARD overrides it."""
import ipaddress, json, os, pathlib, socket, sys, time
from urllib.parse import urlparse

R = pathlib.Path(__file__).resolve().parent.parent
LOG = R / "netguard.log"
CFG = R / "autopost_config.json"
# the platforms and their official APIs: nothing else may receive anything from this PC
BASE = ("youtube.com", "youtu.be", "youtube-nocookie.com", "googlevideo.com", "ytimg.com", "ggpht.com",          # YouTube (+ video/image CDNs)
        "google.com", "googleapis.com", "gstatic.com", "googleusercontent.com",                                    # Google: sign-in, YouTube Data API, Gemini API, static files
        "tiktok.com", "tiktokv.com", "tiktokcdn.com", "tiktokcdn-us.com", "tiktokapis.com", "ttwstatic.com",   # TikTok (+ official API, static files)
        "twitch.tv", "ttvnw.net", "jtvnw.net", "twitchcdn.net", "d1ndex63qxojbr.cloudfront.net")                                                    # Twitch (+ chat IRC, video, API)
PYPI = ("pypi.org", "files.pythonhosted.org")   # only for the daily yt-dlp update check (scripts/update_ytdlp.py sets CLIPPY_ALLOW_PYPI)
ALLOWED_IPS, _seen, _cache = set(), set(), {"t": 0, "cfg": {}}
_orig = {}


def cfg():
    if time.time() - _cache["t"] > 5:
        try:
            _cache["cfg"] = json.loads(CFG.read_text(encoding="utf-8-sig"))
        except Exception:
            _cache["cfg"] = {}
        _cache["t"] = time.time()
    return _cache["cfg"]


def mode():
    m = (os.environ.get("CLIPPY_NETGUARD") or cfg().get("network_guard") or "enforce").lower()  # enforced unless someone deliberately sets log / off
    return m if m in ("enforce", "log", "off") else "enforce"


def allowed_suffixes():
    c = cfg(); out = list(BASE)
    if c.get("title_writer") == "api" or c.get("ai_check"):  # the AI provider's official API address that YOU entered (e.g. api.openai.com)
        for p in c.get("ai_profiles") or []:
            h = urlparse(p.get("url") or "").hostname
            if h:
                out.append(h.lower())
        h = urlparse(c.get("title_api_url") or "").hostname
        if h:
            out.append(h.lower())
    if os.environ.get("CLIPPY_ALLOW_PYPI"):
        out += PYPI
    # one-time model downloads happen only from setup.bat, which sets these after asking you (they are never on during normal runs)
    if os.environ.get("CLIPPY_ALLOW_HF"):
        out += ("huggingface.co", "hf.co")
    if os.environ.get("CLIPPY_ALLOW_UPDATE"):  # only scripts/update_clippy.py: the public Clippy repo (version check + code download)
        out += ("api.github.com", "codeload.github.com")
    if os.environ.get("CLIPPY_ALLOW_GITHUB"):
        out += ("github.com", "raw.githubusercontent.com", "media.githubusercontent.com", "objects.githubusercontent.com")
    return tuple(out)


def _is_ip(h):
    try:
        ipaddress.ip_address(h.strip("[]")); return True
    except ValueError:
        return False


def _loopback(h):
    try:
        return ipaddress.ip_address(h.strip("[]").split("%")[0]).is_loopback
    except ValueError:
        return h.lower() in ("localhost", "localhost.localdomain")


def host_ok(h):
    h = (h or "").lower().rstrip(".")
    if not h or _loopback(h):
        return True
    if _is_ip(h):
        return h in ALLOWED_IPS
    return any(h == s or h.endswith("." + s) for s in allowed_suffixes())


def _note(what):
    key = (os.getpid(), what)
    if key in _seen:
        return
    _seen.add(key)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {'BLOCKED' if mode() == 'enforce' else 'WOULD BLOCK'} {what} (pid {os.getpid()}, {os.path.basename(sys.argv[0]) if sys.argv else '?'})\n")
    except Exception:
        pass


def _deny(what, exc):
    _note(what)
    if mode() == "enforce":
        raise exc


def install():
    if _orig or mode() == "off":
        return

    _orig["gai"], _orig["ghbn"], _orig["connect"], _orig["connect_ex"] = socket.getaddrinfo, socket.gethostbyname, socket.socket.connect, socket.socket.connect_ex

    def getaddrinfo(host, *a, **k):
        h = host.decode() if isinstance(host, (bytes, bytearray)) else host
        good = host_ok(h) if h else True
        if not good and not _is_ip(h or ""):
            _deny(f"dns {h}", socket.gaierror(socket.EAI_NONAME, f"blocked by Clippy network guard: {h}"))
        res = _orig["gai"](host, *a, **k)
        if good and h and not _loopback(h):
            for r in res:
                ALLOWED_IPS.add(r[4][0])
        return res

    def gethostbyname(host):
        if not host_ok(host) and not _is_ip(host):
            _deny(f"dns {host}", socket.gaierror(socket.EAI_NONAME, f"blocked by Clippy network guard: {host}"))
        ip = _orig["ghbn"](host)
        if host_ok(host) and not _loopback(host):
            ALLOWED_IPS.add(ip)
        return ip

    def _check_addr(self, addr):
        if isinstance(addr, tuple) and addr and isinstance(addr[0], str):
            ip = addr[0].split("%")[0]
            if not _loopback(ip) and ip not in ALLOWED_IPS and self.family in (socket.AF_INET, socket.AF_INET6):
                _deny(f"connect {ip}", OSError(f"blocked by Clippy network guard: {ip}"))

    def connect(self, addr):
        _check_addr(self, addr)
        return _orig["connect"](self, addr)

    def connect_ex(self, addr):
        _check_addr(self, addr)
        return _orig["connect_ex"](self, addr)

    socket.getaddrinfo, socket.gethostbyname = getaddrinfo, gethostbyname
    socket.socket.connect, socket.socket.connect_ex = connect, connect_ex
    # model hubs: use the local cache only (a first-ever download needs CLIPPY_ALLOW_HF=1); no analytics from common libraries
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1"); os.environ.setdefault("DO_NOT_TRACK", "1")
    if (pathlib.Path.home() / ".cache" / "huggingface" / "hub").exists() and any((pathlib.Path.home() / ".cache" / "huggingface" / "hub").glob("models--*whisper*")):
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
