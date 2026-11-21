"""Claude-free uploader for YouTube Studio + TikTok Studio (drives a dedicated Chrome profile; full-quality files, no size cap).

  python scripts/upload.py login
      Opens the dedicated Chrome profile normally (NOT automated). Sign in to YouTube/Google and TikTok yourself, then close the window.
  python scripts/upload.py post <clip.mp4> --title T --description D --caption C [--mode private|public] [--only youtube|tiktok]
      Prints one JSON line {"youtube": link|null, "tiktok": "posted"|null, "error": str|null}.
Exit codes: 0 ok, 2 bad args, 3 not signed in, 4 CAPTCHA/verification, 5 failure. Never types credentials; never solves CAPTCHAs.
Selectors target the current Studio UIs and may need maintenance when YouTube/TikTok change them (a screenshot is saved to logs/ on failure)."""
import argparse, json, pathlib, subprocess, sys, time, urllib.request

R = pathlib.Path(__file__).resolve().parent.parent
PROFILE = R / "chrome_profile"
CHROME = next((p for p in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                           r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe") if pathlib.Path(p).exists()), None)
PORT = 9223
NOWIN = 0x08000000


class NotSignedIn(Exception): pass
class Captcha(Exception): pass


def guard(page):
    url = page.url.lower()
    if "accounts.google.com" in url or "/login" in url or "signin" in url:
        raise NotSignedIn(page.url)
    txt = ""
    try:
        txt = page.inner_text("body", timeout=3000).lower()
    except Exception:
        pass
    if any(s in txt for s in ("captcha", "verify you're human", "verify you are human", "drag the slider", "puzzle piece")):
        raise Captcha(page.url)


def browser_mode():
    """config "browser_mode": offscreen (default: real Chrome window beyond the screen edge) | minimized | visible | headless.
    NOTE: YouTube Studio refuses headless Chrome ("unsupported browser"), so headless is off by default; we do not disguise the browser."""
    import os
    if os.environ.get("CLIPPY_BROWSER_MODE") in ("offscreen", "minimized", "visible", "headless"):  # one-off override (tests)
        return os.environ["CLIPPY_BROWSER_MODE"]
    try:
        m = json.loads((R / "autopost_config.json").read_text(encoding="utf-8")).get("browser_mode", "offscreen")
    except Exception:
        m = "offscreen"
    return m if m in ("offscreen", "minimized", "visible", "headless") else "offscreen"


def fullscreen_app_active():
    """True when the window in front covers a whole monitor (a game, a video, a full-screen app). Windows only; the desktop itself does not count."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32; h = u.GetForegroundWindow()
        if not h:
            return False
        cls = ctypes.create_unicode_buffer(64); u.GetClassNameW(h, cls, 64)
        if cls.value in ("Progman", "WorkerW", "Shell_TrayWnd"):
            return False
        r = wintypes.RECT(); u.GetWindowRect(h, ctypes.byref(r))
        class MI(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]
        mi = MI(); mi.cbSize = ctypes.sizeof(MI); u.GetMonitorInfoW(u.MonitorFromWindow(h, 2), ctypes.byref(mi))
        m = mi.rcMonitor
        return r.left <= m.left and r.top <= m.top and r.right >= m.right and r.bottom >= m.bottom
    except Exception:
        return False


def quiet_screen(wait=0):
    """A browser window must never appear over a full-screen game or video: wait (up to `wait` seconds) until none is active. Returns False when it never calmed down.
    CLIPPY_IGNORE_FULLSCREEN=1 skips the check."""
    import os
    if os.environ.get("CLIPPY_IGNORE_FULLSCREEN"):
        return True
    end = time.time() + wait
    while fullscreen_app_active():
        if time.time() >= end:
            return False
        time.sleep(10)
    return True


def start_chrome(port=None, only=None):
    if not CHROME:
        sys.exit("Chrome not found")
    args = [CHROME, f"--user-data-dir={PROFILE}", "--no-first-run", "--no-default-browser-check", "--disable-session-crashed-bubble",
            "--disable-background-mode", "--hide-crash-restore-bubble", "--noerrdialogs",
            # keep Chrome's own chatter minimal: no sync, no background updates / pings / telemetry, so only the sites we open are contacted
            "--disable-background-networking", "--disable-sync", "--disable-component-update", "--disable-default-apps", "--no-pings",
            "--disable-domain-reliability", "--disable-client-side-phishing-detection", "--disable-breakpad", "--metrics-recording-only"]  # closing the window really ends this Chrome (no leftover background process holding the profile)
    if port:
        # No user-agent or automation-flag tricks: if a site blocks a mode, the run stops with a clear message.
        args += [f"--remote-debugging-port={port}", "--window-size=1920,1080", "about:blank"]
        mode = browser_mode()
        # offscreen = normal Chrome window placed beyond the display edge (nothing visible, site sees an ordinary browser)
        args += (["--headless=new"] if mode == "headless" else ["--start-minimized"] if mode == "minimized"
                 else ["--window-position=-3000,0"] if mode == "offscreen" else [])
    else:
        args += ["https://studio.youtube.com", "https://www.tiktok.com/login"]
    return subprocess.Popen(args)


def wait_cdp(port, secs=30):
    end = time.time() + secs
    while time.time() < end:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1); return
        except Exception:
            time.sleep(0.5)
    raise RuntimeError("Chrome debugging port did not open. A Chrome from the sign-in window is probably still running in the background "
                       "and holding the chrome_profile folder: end 'Google Chrome' tasks that use it (Task Manager), or restart the PC, then retry.")


def limit_hit(page):
    """True when YouTube Studio shows its 'daily upload limit reached' message (any interface language we know: English, Romanian)."""
    import re as _re
    try:
        return bool(_re.search(r"daily upload limit|upload limit|limita de .?nc.rcare zilnic", page.inner_text("body", timeout=3000), _re.I))
    except Exception:
        return False


def set_text(page, locator, text):
    loc = page.locator(locator).first
    loc.click(); page.keyboard.press("Control+A"); page.keyboard.press("Delete"); page.keyboard.insert_text(text)


def youtube(page, clip, title, desc, public):
    page.goto("https://studio.youtube.com", wait_until="domcontentloaded"); page.wait_for_timeout(2500); guard(page)
    try:  # Studio redirects to /channel/<id>; the upload dialog opens directly at /channel/<id>/videos/upload?d=ud (language independent)
        page.wait_for_url("**/channel/**", timeout=30000)
    except Exception:
        raise RuntimeError("YouTube Studio did not reach the channel page: the browser may be refused (headless is not accepted) or the page changed. Set browser_mode to minimized or visible")
    import re
    up = f"https://studio.youtube.com/channel/{re.search(r'/channel/([^/?#]+)', page.url).group(1)}/videos/upload?d=ud"
    for attempt in range(3):  # Studio sometimes lands on its dashboard instead of the upload dialog: load the upload address again
        page.goto(up, wait_until="domcontentloaded")
        try:
            page.wait_for_selector("input[type=file]", state="attached", timeout=20000); break
        except Exception:
            if attempt == 2:
                raise
            page.wait_for_timeout(2000)
    page.set_input_files("input[type=file]", str(clip), timeout=300000)
    page.wait_for_timeout(3500)
    if limit_hit(page):  # YouTube refuses the upload: say so clearly instead of timing out on the title box
        raise RuntimeError("YOUTUBE_DAILY_LIMIT: YouTube Studio says the daily upload limit has been reached")
    page.wait_for_selector("#title-textarea #textbox", timeout=90000)
    set_text(page, "#title-textarea #textbox", title)
    set_text(page, "#description-textarea #textbox", desc)
    page.locator("[name='VIDEO_MADE_FOR_KIDS_NOT_MFK']").first.click()  # "No, not made for kids" (GTA is mature)
    for _ in range(3):
        page.wait_for_selector("#next-button:not([disabled]):not([aria-disabled='true'])", timeout=120000)  # selector waits: YouTube's CSP blocks string eval
        page.click("#next-button"); page.wait_for_timeout(1200)
    page.locator(f"[name='{'PUBLIC' if public else 'PRIVATE'}']").first.click()
    link = None
    try:
        link = page.locator("a[href*='youtube.com/shorts'], a[href*='youtu.be'], .video-url-fadeable a").first.get_attribute("href", timeout=8000)
    except Exception:
        pass
    page.wait_for_selector("#done-button:not([disabled]):not([aria-disabled='true'])", timeout=900000)
    page.click("#done-button"); page.wait_for_timeout(4000)
    return link or "uploaded (link not captured)"


def _tt_age_minutes(text, now=None):
    """Minutes since the 'Created on' time shown in a TikTok Studio row ('Nov 21, 10:25 AM'), or None when the row has no such time."""
    import datetime as dt, re as _re
    m = _re.search(r"([A-Z][a-z]{2}) (\d{1,2}), (\d{1,2}):(\d{2})\s?(AM|PM)", text or "")
    if not m:
        return None
    now = now or dt.datetime.now()
    try:
        t = dt.datetime.strptime(f"{m.group(1)} {m.group(2)} {now.year} {m.group(3)}:{m.group(4)} {m.group(5)}", "%b %d %Y %I:%M %p")
    except ValueError:
        return None
    if t > now + dt.timedelta(days=1):
        t = t.replace(year=now.year - 1)  # a December post read in January
    return (now - t).total_seconds() / 60


_TT_FIRST_ROW = """() => { const labels = [...document.querySelectorAll('*')].filter(e => e.children.length === 0 && /^(Everyone|Friends|Only me|Only you)$/.test((e.textContent || '').trim()));
    if (!labels.length) return null; const el = labels[0]; let row = el;
    for (let i = 0; i < 8 && row && !/(AM|PM)/.test(row.innerText || ''); i++) row = row.parentElement;
    return {label: el.textContent.trim(), text: row ? row.innerText.replace(/\\s+/g, ' ').slice(0, 220) : ''}; }"""


def tiktok_privacy_check(page, want_private):
    """After posting, TikTok Studio's list is open: read the privacy of the NEWEST post (the first row, and only if it was created in the last 15 minutes, so an older
    post can never be touched by mistake). If it should be private but is not, switch it to 'Only me' and read it again. Returns 'ok', 'fixed' or a text that
    starts with 'CHECK' (could not confirm: the user must look)."""
    import re as _re
    priv = _re.compile(r"^Only (me|you)$")
    for attempt in range(5):
        page.wait_for_timeout(4000 if attempt == 0 else 8000)
        row = page.evaluate(_TT_FIRST_ROW)
        age = _tt_age_minutes(row["text"]) if row else None
        if row and age is not None and -3 <= age <= 15:
            if priv.match(row["label"]):
                return "ok"
            if row["label"] in ("Everyone", "Friends"):
                h = page.evaluate_handle("() => [...document.querySelectorAll('*')].filter(e => e.children.length === 0 && /^(Everyone|Friends|Only me|Only you)$/.test((e.textContent || '').trim()))[0]")
                h.as_element().click(); page.wait_for_timeout(1200)
                page.locator("[data-floating-ui-portal]").get_by_text(priv).first.click(); page.wait_for_timeout(3000)
                page.reload(wait_until="domcontentloaded"); page.wait_for_timeout(6000)
                again = page.evaluate(_TT_FIRST_ROW)
                return "fixed" if (again and priv.match(again["label"])) else "CHECK: it showed '" + row["label"] + "' and switching it to Only me could not be confirmed"
        page.reload(wait_until="domcontentloaded")  # the new post may not be listed yet
    return "CHECK: the new post did not show up in TikTok Studio's list to confirm its privacy"


def tiktok(page, clip, caption, public):
    page.goto("https://www.tiktok.com/tiktokstudio/upload", wait_until="domcontentloaded"); page.wait_for_timeout(3000); guard(page)
    page.wait_for_selector("input[type=file]", state="attached", timeout=30000)
    page.set_input_files("input[type=file]", str(clip), timeout=300000)
    page.wait_for_selector("text=Uploaded", timeout=600000)
    for label in ("Decline optional cookies", "Cancel", "Got it"):  # cookie banner (covers the Post button): decline optional cookies; "automatic content checks" popup: decline; tips
        try:
            page.get_by_role("button", name=label, exact=True).first.click(timeout=2500)
        except Exception:
            pass
    set_text(page, "[contenteditable='true']", caption)
    page.mouse.click(5, 300)  # close hashtag suggestions
    if not public:
        page.get_by_text("Everyone", exact=True).first.click(); page.wait_for_timeout(500)
        import re as _re
        opts = page.locator("[data-floating-ui-portal]").get_by_text(_re.compile(r"^Only (me|you)$"))  # the open menu itself, so another 'Only me' on the page cannot be clicked by mistake
        (opts.first if opts.count() else page.get_by_text(_re.compile(r"^Only (me|you)$")).first).click(); page.wait_for_timeout(500)  # TikTok renamed "Only me" to "Only you"
        if page.get_by_text("Everyone", exact=True).count() and page.get_by_text(_re.compile(r"^Only (me|you)$")).count() == 0:
            raise RuntimeError("could not set TikTok visibility to private: stopping before Post")
    btn = page.get_by_role("button", name="Post", exact=True).first
    btn.scroll_into_view_if_needed(); btn.click()
    try:
        page.get_by_role("button", name="Post now", exact=True).first.click(timeout=4000)  # copyright-check confirm, if shown
    except Exception:
        pass
    page.wait_for_url("**/tiktokstudio/content**", timeout=120000)
    if public:
        return "posted"
    try:  # a private post must really be private: read it back from the list and correct it if TikTok left it on Everyone
        note = tiktok_privacy_check(page, True)
    except Exception as e:
        note = f"CHECK: could not read the privacy back ({type(e).__name__})"
    return "posted" if note == "ok" else "posted (privacy corrected to Only me)" if note == "fixed" else f"posted (PRIVACY_CHECK {note})"


def cmd_login():
    print("Chrome opened with the dedicated profile. Sign in to YouTube/Google and TikTok, then close the window.")
    start_chrome().wait()


def cmd_status():
    """Open the YouTube and TikTok upload pages in the dedicated profile and report whether each is signed in -> signin_status.json."""
    from playwright.sync_api import sync_playwright
    import datetime as dt
    out = {"youtube": None, "tiktok": None, "ts": dt.datetime.now().isoformat(timespec="seconds")}
    if not quiet_screen(0):  # a full-screen app is in front: do not open a window now, keep the last known result
        print(json.dumps({"skipped": "a full-screen app is active"})); return 8
    proc = start_chrome(PORT)
    try:
        wait_cdp(PORT)
        with sync_playwright() as p:
            b = p.chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}")
            ctx = b.contexts[0]
            for name, url in (("youtube", "https://studio.youtube.com"), ("tiktok", "https://www.tiktok.com/tiktokstudio/upload")):
                page = ctx.new_page()
                try:
                    page.goto(url, wait_until="domcontentloaded"); page.wait_for_timeout(4000); guard(page)
                    # logged in only counts if the real upload UI is there (an "unsupported browser" page is neither signed in nor out)
                    if name == "youtube":
                        page.wait_for_url("**/channel/**", timeout=20000)
                    else:
                        page.wait_for_selector("input[type=file]", state="attached", timeout=15000)
                    out[name] = True
                except NotSignedIn:
                    out[name] = False
                except Exception:
                    out[name] = None  # CAPTCHA or page error: unknown
                finally:
                    page.close()
            b.close()
    finally:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], creationflags=NOWIN, capture_output=True)
    (R / "signin_status.json").write_text(json.dumps(out), encoding="utf-8"); print(json.dumps(out)); return 0


def cmd_post(a):
    from playwright.sync_api import sync_playwright
    clip = pathlib.Path(a.clip).resolve()
    if not clip.exists():
        print(json.dumps({"error": "clip not found"})); return 2
    res = {"youtube": None, "tiktok": None, "error": None}
    if browser_mode() != "headless" and not quiet_screen(1800):  # never open a window over a game or video: wait up to 30 min
        print(json.dumps({"youtube": None, "tiktok": None, "error": "a full-screen app stayed active for 30 minutes: nothing was posted, try again when you are done"})); return 8
    proc = start_chrome(PORT, a.only); code = 0; errors = []
    try:
        wait_cdp(PORT)
        with sync_playwright() as p:
            b = p.chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}")
            ctx = b.contexts[0]
            for name, fn in (("youtube", lambda pg: youtube(pg, clip, a.title, a.description, a.mode == "public")),
                             ("tiktok", lambda pg: tiktok(pg, clip, a.caption, a.mode == "public"))):
                if a.only and a.only != name:
                    continue
                page = ctx.new_page()
                try:
                    res[name] = fn(page)
                    if "PRIVACY_CHECK" in str(res[name]):  # it WAS posted, but its privacy could not be confirmed: tell the user loudly
                        errors.append(f"{name}: {res[name]}"); code = code or 6
                except NotSignedIn as e:  # one platform failing never stops the other: each is tried, errors are collected
                    errors.append(f"{name}: not signed in ({e}). Run: upload.py login"); code = code or 3
                except Captcha as e:
                    errors.append(f"{name}: CAPTCHA/verification ({e}). Handle it yourself, then rerun"); code = code or 4
                except Exception as e:
                    (R / "logs").mkdir(exist_ok=True)
                    shot = R / "logs" / f"upload_fail_{name}_{int(time.time())}.png"
                    try: page.screenshot(path=str(shot))
                    except Exception: pass
                    why = "YOUTUBE_DAILY_LIMIT: YouTube Studio says the daily upload limit has been reached" if (name == "youtube" and "YOUTUBE_DAILY_LIMIT" not in str(e) and limit_hit(page)) else f"{type(e).__name__}: {str(e)[:300]}"
                    errors.append(f"{name}: {why} (screenshot: {shot.name})"); code = code or 5
                finally:
                    try: page.close()
                    except Exception: pass
            b.close()
    except Exception as e:
        errors.append(f"{type(e).__name__}: {e}"); code = code or 5
    finally:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], creationflags=NOWIN, capture_output=True)
    res["error"] = " | ".join(errors) or None
    print(json.dumps(res)); return code


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("login"); sp.add_parser("status")
    q = sp.add_parser("post"); q.add_argument("clip"); q.add_argument("--title", required=True); q.add_argument("--description", required=True)
    q.add_argument("--caption", required=True); q.add_argument("--mode", choices=["private", "public"], default="private"); q.add_argument("--only", choices=["youtube", "tiktok"])
    a = ap.parse_args()
    sys.exit({"login": lambda: cmd_login() or 0, "status": cmd_status, "post": lambda: cmd_post(a)}[a.cmd]())
