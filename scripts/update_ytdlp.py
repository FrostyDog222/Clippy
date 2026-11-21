"""Daily yt-dlp update check (YouTube changes constantly; an old yt-dlp stops downloading).
python scripts/update_ytdlp.py [--force] [--before-run]    checks PyPI (the dashboard once a day; every run also checks first when the last check is over 6 h old),
installs a newer yt-dlp[default] into this project's venv, runs a self-test, rolls back to the previous version when the test fails,
and keeps requirements.txt / requirements.lock.txt in sync.
Only pypi.org / files.pythonhosted.org are contacted (allowed for this process only through CLIPPY_ALLOW_PYPI). State: ytdlp_update.json."""
import importlib.metadata as md, json, os, pathlib, re, subprocess, sys, time

R = pathlib.Path(__file__).resolve().parent.parent
PY = str(R / ".venv" / "Scripts" / "python.exe")
STATE = R / "ytdlp_update.json"
NOWIN = 0x08000000
env = {**os.environ, "CLIPPY_ALLOW_PYPI": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1"}


def say(m):
    print(m, flush=True)


def busy():
    r = subprocess.run(["powershell", "-NoProfile", "-Command",
                        "(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'autorun\\.py|live_watch\\.py|scripts.live\\.py|moments\\.py|upload\\.py|stats\\.py' }).Count"],
                       capture_output=True, text=True, creationflags=NOWIN)
    try:
        return int(r.stdout.strip() or 0) > 0
    except ValueError:
        return False


def latest():
    import requests
    os.environ["CLIPPY_ALLOW_PYPI"] = "1"  # the network guard lets pypi.org through only for this one request
    try:
        return requests.get("https://pypi.org/pypi/yt-dlp/json", timeout=30).json()["info"]["version"]
    finally:
        os.environ.pop("CLIPPY_ALLOW_PYPI", None)


def vtuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", v)[:4])


def selftest():
    r = subprocess.run([PY, "-c", "import yt_dlp; yt_dlp.YoutubeDL({'quiet': True}); print(yt_dlp.version.__version__)"], capture_output=True, text=True, timeout=120)
    return r.returncode == 0, r.stdout.strip()


def pin(version):
    for f, pat, new in (("requirements.txt", r"^yt-dlp\[default\]==.*$", f"yt-dlp[default]=={version}"), ("requirements.lock.txt", r"^yt-dlp==.*$", f"yt-dlp=={version}")):
        p = R / f
        if p.exists():
            t = p.read_text(encoding="utf-8").splitlines()
            p.write_text("\n".join(re.sub(pat, new, ln) for ln in t) + "\n", encoding="utf-8")


def main(force=False, before_run=False):
    """before_run=True (called at the start of every run): re-check when the last check is older than 6 h, and do not wait for other Clippy jobs.
    Otherwise (dashboard, once a day): 24 h, and wait while a job is running."""
    st = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    max_age = (6 if before_run else 24) * 3600
    if not force and time.time() - st.get("checked_ts", 0) < max_age:
        say(f"yt-dlp checked {int((time.time() - st['checked_ts']) / 3600)} h ago"); return 0
    if not before_run and busy():
        say("a Clippy job is running: the yt-dlp check waits"); return 0
    cur = md.version("yt-dlp")
    try:
        new = latest()
    except Exception as e:
        say(f"could not reach PyPI ({str(e)[:80]}): will retry"); return 0
    st.update({"checked": time.strftime("%Y-%m-%d %H:%M"), "checked_ts": time.time(), "installed": cur, "latest": new})
    if vtuple(new) <= vtuple(cur):
        st["result"] = "up to date"; STATE.write_text(json.dumps(st, indent=1), encoding="utf-8"); say(f"yt-dlp {cur} is up to date"); return 0
    say(f"updating yt-dlp {cur} -> {new}")
    r = subprocess.run([PY, "-m", "pip", "install", "-q", f"yt-dlp[default]=={new}"], capture_output=True, text=True, env=env, timeout=900)
    ok, ver = selftest()
    if r.returncode != 0 or not ok:
        subprocess.run([PY, "-m", "pip", "install", "-q", f"yt-dlp[default]=={cur}"], capture_output=True, text=True, env=env, timeout=900)
        st["result"] = f"update to {new} failed, rolled back to {cur}"
        say(st["result"] + ": " + (r.stderr or "")[-200:])
    else:
        pin(new); st["result"] = f"updated {cur} -> {ver}"; say(st["result"])
    STATE.write_text(json.dumps(st, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(force="--force" in sys.argv, before_run="--before-run" in sys.argv))
