"""Performance tracker: reads views / likes / comments of the clips Clippy posted and stores them in autopost_state.json (post["stats"]).
YouTube: public Shorts via yt-dlp (a Private video has no public numbers). TikTok: the TikTok Studio "Posts" list in the signed-in browser profile.
python scripts/stats.py          refresh now (waits for the upload lock: never runs while a clip is uploading)
python scripts/stats.py --dump   print the raw TikTok Studio text (debugging)
Never posts, edits or deletes anything."""
import datetime as dt, json, pathlib, re, subprocess, sys, time

R = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(R)); sys.path.insert(0, str(R / "scripts"))
import upload  # noqa: E402

PY = str(R / ".venv" / "Scripts" / "python.exe")
STATE = R / "autopost_state.json"
NOWIN = 0x08000000
MONTHS = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def num(s):
    """'1,234' -> 1234, '12.5K' -> 12500, '1.2M' -> 1200000, '-' / '' -> None."""
    s = (s or "").strip().replace(",", "")
    m = re.fullmatch(r"([\d.]+)\s*([KMB]?)", s, re.I)
    if not m:
        return None
    return int(float(m.group(1)) * {"": 1, "K": 1e3, "M": 1e6, "B": 1e9}[m.group(2).upper()])


def yt_stats(link):
    if not link or "youtube.com" not in link:
        return None
    r = subprocess.run([PY, "-m", "yt_dlp", "--no-warnings", "--skip-download", "--print", "%(view_count)s|%(like_count)s|%(comment_count)s", link],
                       capture_output=True, text=True, timeout=120, creationflags=NOWIN)
    if r.returncode != 0 or "|" not in r.stdout:
        return None  # private / removed
    v, l, c = (num(x) if x != "NA" else None for x in r.stdout.strip().splitlines()[-1].split("|"))
    return {"views": v, "likes": l, "comments": c}


def tiktok_text(mode="headless"):
    """Reads the TikTok Studio posts list. Default: a HEADLESS browser (no window at all, so it can never pop up over a game or take focus; TikTok accepts it, YouTube does not)."""
    from playwright.sync_api import sync_playwright
    proc = upload.start_chrome(upload.PORT, mode=mode)
    try:
        upload.wait_cdp(upload.PORT)
        with sync_playwright() as p:
            b = p.chromium.connect_over_cdp(f"http://127.0.0.1:{upload.PORT}")
            page = b.contexts[0].new_page()
            try:
                page.goto("https://www.tiktok.com/tiktokstudio/content", wait_until="domcontentloaded"); page.wait_for_timeout(7000)
                upload.guard(page)
                return page.inner_text("body")
            finally:
                page.close(); b.close()
    finally:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], creationflags=NOWIN, capture_output=True)


def parse_tiktok(text):
    """Rows of the Studio posts list: [(created datetime, privacy, views, likes, comments)] from the page text.
    A row reads: caption lines, 'Nov 19, 6:16 PM', privacy, views, likes, comments."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    rows = []
    for i, l in enumerate(lines):
        m = re.fullmatch(r"([A-Z][a-z]{2})\s+(\d{1,2}),\s+(\d{1,2}):(\d{2})\s*([AP]M)", l)  # \s: TikTok may use a narrow no-break space before AM/PM
        if not m or i + 4 >= len(lines):
            continue
        h = int(m.group(3)) % 12 + (12 if m.group(5) == "PM" else 0)
        when = dt.datetime(dt.date.today().year, MONTHS[m.group(1)], int(m.group(2)), h, int(m.group(4)))
        priv, v, li, c = lines[i + 1], num(lines[i + 2]), num(lines[i + 3]), num(lines[i + 4])
        if v is not None:
            rows.append((when, priv, v, li, c))
    return rows


def refresh():
    st = json.loads(STATE.read_text(encoding="utf-8"))
    posts = [p for p in st.get("posts", []) if not p.get("deleted")]
    now = dt.datetime.now().isoformat(timespec="seconds")
    try:
        rows = parse_tiktok(tiktok_text())
        if not rows and any(p.get("tiktok_status") for p in posts) and upload.quiet_screen(0):  # the hidden browser saw nothing (a login wall?): one try with the off-screen window, never while a full-screen app is active
            rows = parse_tiktok(tiktok_text("offscreen"))
    except Exception as e:
        print(f"TikTok numbers unavailable: {type(e).__name__}: {str(e)[:120]}"); rows = []
    done = 0
    for p in posts:
        snap = {"ts": now}
        yt = None
        try:
            yt = yt_stats(p.get("youtube_link"))
        except Exception:
            pass
        if yt:
            snap["yt"] = yt
        if p.get("ts"):
            t0 = dt.datetime.fromisoformat(p["ts"])
            best = min(rows, key=lambda r: abs((r[0] - t0).total_seconds()), default=None)
            if best and abs((best[0] - t0).total_seconds()) <= 240:  # same minute window: match by posting time
                snap["tt"] = {"views": best[2], "likes": best[3], "comments": best[4]}
        if "yt" in snap or "tt" in snap:
            p.setdefault("stats", []).append(snap); p["stats"] = p["stats"][-40:]; done += 1
    STATE.write_text(json.dumps(st, indent=1), encoding="utf-8")
    print(f"stats updated for {done} of {len(posts)} posts ({len(rows)} TikTok rows read)")
    return 0


if __name__ == "__main__":
    if "--dump" in sys.argv:
        print(tiktok_text()); sys.exit(0)
    import autorun
    with autorun.post_lock(wait=600):  # shares the one signed-in browser profile with uploads
        sys.exit(refresh())
