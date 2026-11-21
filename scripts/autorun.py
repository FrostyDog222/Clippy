"""One fully automatic run WITHOUT Claude: pick source -> find strong moment -> cut -> check -> post (post_clip.py) -> clean up.
python scripts/autorun.py [--force] [--dry]     --force ignores tick.py (daily limit/window/gap), --dry stops before posting
Source order: 1) the user's streamer list (rotate/random/fixed)  2) if "use_trending_fallback" is ON: what is trending/popular for GTA 6 right now
(also used when the list is empty). List empty AND trending OFF -> nothing to do (the dashboard refuses to start the bot in that state).
Quality rules: only a moment scored >= STRONG by moments.py; weak sources are remembered in autopost_state.json ("rejected") and skipped.
Limits: hook text is a plain template (no AI); there is no visual check of the clip beyond the technical check."""
import argparse, datetime as dt, json, pathlib, random, re, shutil, subprocess, sys, time

import os as _os
_os.environ["PYTHONUTF8"] = "1"  # every child process this script starts then writes UTF-8 too: a video title with an emoji must never crash a run (the old Windows text encoding cannot hold it)
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

R = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(R)); sys.path.insert(0, str(R / "scripts"))
import tick  # noqa: E402
from common import youtube_cookie_args, ytdlp_json  # noqa: E402
CUSTOM = None  # a run on one link the user chose: {"clips": n, "start": minute or None, "len": minutes or None} (see _custom)

PY = str(R / ".venv" / "Scripts" / "python.exe")
BANNED = re.compile(r"\b(leak|leaked|early access|play early|free download|download free|crack|piracy|cheat engine|giveaway)\b", re.I)
# informational / news / analysis videos: voice-over talking about the game, not someone playing or reacting
NEWS = re.compile(r"\b(news|everything new|new screenshots?|screenshots?|trailer|breakdown|explained|explaining|everything (you need|we know)|all you need|"
                  r"release date|pre-?orders?|price|tier list|top \d+|things you missed|details? you|secrets?|leaks?|rumou?rs?|predictions?|theor(y|ies)|"
                  r"reaction to the (trailer|news)|before (gta 6|it) drops|countdown|update|patch notes|how to|guide|tips?|tutorial)\b", re.I)
import topic as topic_mod  # noqa: E402
TP = topic_mod.info({})  # set from the config in configure(): what to look for (a game / subject, or whatever is trending)
norm = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())
LIMITS = (18, 30)  # clip length in seconds (min, max); set from config "clip_seconds" in _main
LAYOUTS = ("facecam", "blur")  # tried in this order; set from config "layout"
ORIGIN = "video"  # "live" when called by live_watch.py
CHAT_HINT = None  # live mode: what chat said at the spike (list of messages), set by live_watch.py
CFG = {}  # loaded config (title_writer: template | claude | api)


def log(msg):
    print(time.strftime("%H:%M:%S  ") + str(msg), flush=True)  # every log line starts with the time


def progress(pct, msg):
    subprocess.run([PY, str(R / "scripts" / "progress.py"), str(pct), msg])


def sh(args, **kw):
    return subprocess.run(args, cwd=R, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env={**__import__("os").environ, "CLIP_DESK_HOME": str(R), "PYTHONIOENCODING": "utf-8", "HF_HUB_DISABLE_SYMLINKS_WARNING": "1"}, **kw)


CACHE_F = R / "search_cache.json"
BLOCK_F = R / "yt_block.json"
BOT_HINTS = ("not a bot", "confirm you", "confirm you’re")


def _cache():
    try:
        return json.loads(CACHE_F.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _cache_put(c):
    try:
        CACHE_F.write_text(json.dumps(c), encoding="utf-8")
    except Exception:
        pass


def yt_blocked():
    """Seconds left on the cooldown after YouTube asked us to 'confirm you are not a bot' (0 = free). Hammering YouTube during a block only makes it last longer."""
    try:
        return max(0, int(json.loads(BLOCK_F.read_text(encoding="utf-8"))["until"] - time.time()))
    except Exception:
        return 0


def note_block(text):
    if any(h in (text or "").lower() for h in BOT_HINTS):
        BLOCK_F.write_text(json.dumps({"until": time.time() + 3 * 3600, "at": time.strftime("%Y-%m-%d %H:%M")}), encoding="utf-8")
        log("  YouTube asked to confirm we are not a bot: pausing YouTube video requests for 3 hours"); return True
    return False


def recent(vid, days=3):
    """upload date within the last N days (flat search results carry no date, so ask yt-dlp for this one video)."""
    c = _cache(); key = f"date:{vid}"
    ds = c.get(key)  # an upload date never changes: ask YouTube once
    if not ds:
        r = sh([PY, "-m", "yt_dlp", "--no-warnings", *youtube_cookie_args(), "--skip-download", "--print", "%(upload_date)s", f"https://www.youtube.com/watch?v={vid}"])
        note_block(r.stderr)
        try:
            ds = r.stdout.strip().splitlines()[-1]; dt.datetime.strptime(ds, "%Y%m%d"); c[key] = ds; _cache_put(c)
        except Exception:
            return False
    try:
        return (dt.date.today() - dt.datetime.strptime(ds, "%Y%m%d").date()).days <= days
    except Exception:
        return False


def flat_search(query):
    c = _cache(); key = f"search:{query}"; hit = c.get(key)
    if hit and time.time() - hit["t"] < 1800:  # the same search within 30 min reuses the answer (fewer requests, fewer blocks)
        return hit["entries"]
    r = sh([PY, "-m", "yt_dlp", "--no-warnings", *youtube_cookie_args(), "--flat-playlist", "-J", f"ytsearch15:{query}"])
    note_block(r.stderr)
    try:
        entries = json.loads(r.stdout).get("entries", [])
    except Exception:
        return []
    c[key] = {"t": time.time(), "entries": entries}; _cache_put(c)
    return entries


def usable(e):
    """Common filters: the topic from the dashboard, not live, sane length, no banned wording."""
    t = e.get("title") or ""
    if e.get("live_status") in ("is_live", "is_upcoming") or not e.get("duration"):
        return None
    if not TP["matches"](t) or BANNED.search(t) or NEWS.search(t) or not (150 <= e["duration"] <= 12 * 3600):
        return None
    letters = [ch for ch in t if ch.isalpha()]
    if textutil.allowed_languages(CFG) == ["en"] and letters and sum(ch.isascii() for ch in letters) / len(letters) < 0.85:  # English only: skip titles written in other scripts / heavy accents
        return None
    return {"id": e["id"], "title": t, "channel": e.get("channel") or e.get("uploader") or "?", "duration": e["duration"], "views": e.get("view_count") or 0}


def search_creator(creator):
    out = []
    for e in flat_search(TP["creator_query"](creator)):
        ch = e.get("channel") or e.get("uploader") or ""
        if ch and (norm(creator) in norm(ch) or norm(ch) in norm(creator)):
            v = usable(e)
            if v:
                out.append(v)
    out = sorted(out, key=lambda x: (x["duration"] > 3 * 3600, -x["views"]))[:8]  # the caller drops used/rejected ones, so look a bit further down the list
    return [v for v in out if recent(v["id"])]


def search_twitch_clips(skip_ids):
    """Twitch clips viewers made in the last day on the topic (or the most watched categories when trending-any): 15-60 s, most viewed first."""
    import twitch
    game = None if TP["any"] else ("Grand Theft Auto VI" if TP["gta"] else TP["name"])
    out = []
    for c in twitch.top_clips(game, 8):
        if c["id"] in skip_ids or BANNED.search(c["title"]) or not (15 <= c["duration"] <= 60):
            continue
        out.append({**c, "twitch": True})
    return out[:6]


def search_trending(skip_channels, skip_ids):
    """Popular videos on the topic (or just whatever is trending, when no topic is set) right now across YouTube (one video per channel), most viewed first, only the last few days."""
    best = {}
    for q in TP["queries"]:
        for e in flat_search(q):
            v = usable(e)
            if v and v["id"] not in skip_ids and norm(v["channel"]) not in skip_channels and (norm(v["channel"]) not in best or v["views"] > best[norm(v["channel"])]["views"]):
                best[norm(v["channel"])] = v
    ranked = sorted(best.values(), key=lambda x: (x["duration"] > 3 * 3600, -x["views"]))[:12]  # popular first, but multi-hour streams (slow: 15+ min per slice) after the rest
    out = []
    for v in ranked:
        if recent(v["id"]):
            out.append(v)
        if len(out) >= 5:
            break
    return out


def strong_candidates(v):
    """Run moments.py (slices for long videos); return [(score, job, pick_n, cand)] for strong moments only."""
    url = v.get("url") or f"https://www.youtube.com/watch?v={v['id']}"  # Twitch clips carry their own url
    mins = v["duration"] / 60
    plans = [[]] if mins <= 25 else [["--start", str(int(mins * f)), "--max-minutes", "10"] for f in (0.12, 0.45, 0.75)]  # three slices spread over the video (a long video was only sampled twice)
    if CUSTOM and CUSTOM.get("start") is not None:  # the user named the part to look at (minutes into the video, and how many minutes)
        plans = [["--start", str(CUSTOM["start"]), "--max-minutes", str(CUSTOM.get("len") or 12)]]
    found = []
    for extra in plans:
        try:
            r = sh([PY, "scripts/moments.py", url, "--top", "6", "--min", str(LIMITS[0]), "--max", str(LIMITS[1]), *extra], timeout=1500)
        except subprocess.TimeoutExpired:
            log("  analysis took longer than 25 min: giving up on this video"); break
        m = re.search(r"job: (.+)", r.stdout)
        if not m:
            log("  moments failed: " + (r.stderr or r.stdout)[-200:]); v["_dl_failed"] = True  # a technical failure, not a verdict on the video
            if note_block(r.stderr + r.stdout): break  # blocked: stop asking
            continue
        job = pathlib.Path(m.group(1).strip())
        cj = job / "candidates.json"; cd = json.loads(cj.read_text(encoding="utf-8")); cands = cd["candidates"]
        wins = None
        try:  # the AI scores every window of the transcript; its ranking decides together with the local detector (see ai_check.score_windows)
            import ai_check
            wins = ai_check.score_windows(CFG, json.loads((job / "transcript.json").read_text(encoding="utf-8")))
        except Exception as e:
            log(f"  AI window scoring unavailable ({str(e)[:60]})")
        if wins:
            log(f"  AI scored {len(wins)} windows; best: " + ", ".join(f"{w['start']:.0f}s={w['score']:g}" for w in wins[:4]))
            for c in cands:  # local picks: remember the best AI window score that covers them
                cov = [w["score"] for w in wins if w["start"] <= c["peak"] <= w["end"]]
                c["ai"] = max(cov) if cov else None
            nxt = max([c["n"] for c in cands] or [0]) + 1
            for w in wins[:4]:  # AI-ranked windows the local detector did not pick become candidates too (the bit finder trims them later)
                if w["score"] >= 6 and not any(c["start"] < w["end"] and w["start"] < c["end"] for c in cands):
                    cands.append({"n": nxt, "start": round(w["start"], 1), "end": round(min(w["end"], w["start"] + LIMITS[1]), 1), "peak": round((w["start"] + w["end"]) / 2),
                                  "score": 5.0, "strong": True, "why": ["ranked by the AI: " + w["why"]], "text": w["text"][:400], "sig": {}, "ai": w["score"]})
                    nxt += 1
            cj.write_text(json.dumps(cd, ensure_ascii=False, indent=1), encoding="utf-8")
        if (v.get("twitch") or (v.get("custom") and cd.get("duration", 999) <= 120)) and not any(c.get("strong") for c in cands) and cd.get("duration"):  # a viewer-made clip IS the moment: the whole clip is the candidate
            cands.append({"n": max([c["n"] for c in cands] or [0]) + 1, "start": 0.0, "end": round(float(cd["duration"]), 1), "peak": int(cd["duration"] / 2), "score": 5.0, "strong": True,
                          "why": [f"Twitch clip with {v['views']} views"], "text": " ".join(w["w"] for w in json.loads((job / "transcript.json").read_text(encoding="utf-8")))[:400], "sig": {}})
            cj.write_text(json.dumps(cd, ensure_ascii=False, indent=1), encoding="utf-8")
        for c in cands:
            if c.get("strong") or v.get("custom"):  # a link the user chose: the best moments even when the detector rates them weak (the quality checks still judge them)
                found.append((c["score"], job, c["n"], c))
    return sorted(found, key=lambda x: -(x[0] + 0.5 * ((x[3].get("ai") or 5) - 5)))  # local score plus the AI window score


from textutil import quote_hook  # noqa: E402
import ai_title  # noqa: E402
import textutil  # noqa: E402


def seam_title(quote, name):
    """Short on-video title: the real quote when there is one (people's own words are the funniest). No quote -> None (no title beats a bland
    "NAME ON GTA 6"); a funny/catchy one then needs the AI title writer."""
    return quote or None


def display_name(v, creator):
    """Name used in the hook: the name from the user's list when the video came from it, else the channel minus Live/raw/clips."""
    return creator or re.sub(r"\s+(live|raw|clips|highlights|vods?)$", "", v["channel"], flags=re.I).strip()


MIN_FACE_MOTION = 3.5  # measured: talking streamer 7.4, visibly laughing 4.3, quiet/static streamer 2.2 (mean frame change in the webcam panel)


def face_motion(clip):
    """Mean frame-to-frame change in the webcam panel (top 720 px of the 1080x1920 clip). Low = the streamer is not reacting on camera."""
    import numpy as np
    w, h = 160, 107
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", str(clip), "-vf", f"fps=6,crop=1080:720:0:0,scale={w}:{h},format=gray", "-f", "rawvideo", "-"], capture_output=True)
    fr = np.frombuffer(p.stdout, dtype=np.uint8)
    fr = fr[: len(fr) // (w * h) * (w * h)].reshape(-1, w * h).astype(np.float32)
    return float(np.abs(np.diff(fr, axis=0)).mean()) if len(fr) > 1 else 0.0


MIN_FACE_COVERAGE = 0.75  # share of sampled frames whose webcam panel really shows a face (the streamer may switch OBS scenes mid-clip)


def face_coverage(clip):
    """Sample 12 frames of the finished clip, crop the top (webcam) panel, run the YuNet face detector. Returns the fraction with a face."""
    import facecam
    frames = facecam.sample_frames(clip, 0, None, 12)
    tops = [f[: int(f.shape[0] * 720 / 1920)] for f in frames]
    det = facecam.detect_faces(tops)
    return sum(1 for d in det if d) / max(1, len(det))


import contextlib, os, time  # noqa: E402


@contextlib.contextmanager
def post_lock(wait=3600):
    """Exclusive lock file; a lock older than 1 h is treated as stale (crashed run)."""
    lf = R / "post.lock"; end = time.time() + wait
    while True:
        try:
            os.close(os.open(lf, os.O_CREAT | os.O_EXCL)); break
        except FileExistsError:
            if time.time() - lf.stat().st_mtime > 3600:
                lf.unlink(missing_ok=True)
            elif time.time() > end:
                raise TimeoutError("another upload is still running")
            time.sleep(5)
    try:
        yield
    finally:
        lf.unlink(missing_ok=True)


REVIEW = R / "review"  # finished clips waiting for the user (dashboard "Ready to post"); outside jobs/ so cleanup never touches them


def review_ids():
    """Source ids of clips already waiting in the review folder (so the same video is not clipped again before the user decided)."""
    out = set()
    for f in REVIEW.glob("*__clip_*.json") if REVIEW.exists() else []:
        try:
            sid = json.loads(f.read_text(encoding="utf-8")).get("source_id")
            if sid:
                out.add(sid)
        except Exception:
            pass
    return out


def queue_for_review(clip):
    """Copy the finished clip + its json into review/ and write the title/description file; the dashboard shows it with a Post button. Returns the stem."""
    REVIEW.mkdir(exist_ok=True)
    stem = f"{int(time.time())}__{clip.stem}"  # must keep "clip_N": post_clip.py derives the meta file name from it
    shutil.copy2(clip, REVIEW / f"{stem}.mp4")
    d = json.loads(clip.with_suffix(".json").read_text(encoding="utf-8")); d["file"] = str(REVIEW / f"{stem}.mp4")
    (REVIEW / f"{stem}.json").write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    sh([PY, "scripts/make_meta.py", str(REVIEW / f"{stem}.json")])
    return stem


def _noai(c, q, hook, seam, scene):
    """No-AI titles: a real quote stays the on-video title; otherwise a clean line for the recognised event (plane, crash, police...)."""
    n = textutil.noai_titles(f"{c.get('text', '')} {scene or ''}", CHAT_HINT)
    if n["event"]:  # something specific was recognised: its title fits better than "<name> reacts to GTA 6"
        hook = n["youtube_title"]
    if seam and not (textutil.REACT.search(seam) and len(seam.split()) >= 4):  # a quote is only worth the big title when it is a clear, reaction-like sentence
        seam = None
    out = (hook, (seam or n["on_video"]), n["tiktok_line"])
    return out if TP["gta"] else tuple(x.replace("GTA 6", TP["name"]) if isinstance(x, str) else x for x in out)  # the fixed banks name GTA 6: use the dashboard topic instead


def snap_start(src, start):
    """Start the clip on a real scene cut when one sits just before the planned start (so it does not begin mid-transition). Returns the new start or None."""
    t0 = max(0.0, start - 4.0)
    try:
        r = subprocess.run(["ffmpeg", "-v", "info", "-ss", f"{t0:.2f}", "-t", "4.6", "-i", str(src), "-vf", "select='gt(scene,0.4)',showinfo", "-an", "-f", "null", "-"],
                           capture_output=True, text=True, timeout=120)
        cuts = [t0 + float(m) for m in re.findall(r"pts_time:([0-9.]+)", r.stderr)]
    except Exception:
        return None
    ok = [x for x in cuts if start - 3.5 <= x <= start + 0.3]
    return max(ok) + 0.05 if ok else None


def judge(v, score, job, n, c, creator):
    """The checks of one moment (score bar, AI bit finder, AI preview / frame check, visual check). Returns None (not good enough) or what the cut needs plus a rank."""
    log(f"  moment {n}: score {score} ({', '.join(c['why'])})")
    strict = CFG.get("quality", "strict") == "strict"  # quality over quantity is ON unless the user switches it off
    if strict and ORIGIN != "live" and score < 5.0:  # the viral score (voice, laughter, swearing, action, chat...): 5+ = clearly strong; relaxed accepts 4+ (see moments.STRONG)
        log(f"  viral score {score} is below the 5.0 bar of 'quality over quantity', skipping"); return None
    cut_range = []
    win = None
    scene, win_score, res_score = None, None, None
    if CFG.get("title_writer") in ("claude", "api"):  # an AI reads what is said (silences included) and finds where the bit really starts and ends
        try:
            import ai_check
            allw = json.loads((job / "transcript.json").read_text(encoding="utf-8"))
            win = ai_check.refine_window(CFG, allw, c["start"], c["end"], LIMITS[1])
        except Exception as e:
            win = None; log(f"  AI bit finder unavailable ({str(e)[:60]})")
        if not win and strict and ORIGIN != "live" and CFG.get("title_writer") == "api":  # nothing real judged this moment (no AI answer): the local score alone is not enough
            log("  no AI read of what is said in this moment, skipping (quality over quantity)"); return None
        if win:
            ws, we = win["start"], win["end"]
            log(f"  AI bit finder: {c['start']:.0f}-{c['end']:.0f}s -> {ws:.0f}-{we:.0f}s ({we - ws:.0f}s) score {win['score']:g}/10, speaker {win['speaker']}, "
                f"standalone {win['standalone']}{', risky (flag only)' if win['risky'] else ''}: {win['why']}")
            if strict and ORIGIN != "live" and (win["score"] < 6 or (win["speaker"] == "game" and win["score"] < 7) or (not win["standalone"] and win["score"] < 6)):
                log("  the AI rates this bit as weak (game-only dialogue or not standalone), skipping"); return None
            win_score = win["score"]
            c = {**c, "start": ws, "end": we, "text": " ".join(w["w"] for w in allw if ws <= w["s"] < we)[:400]}
            cut_range = ["--start", f"{ws:.2f}", "--end", f"{we:.2f}"]
    if CFG.get("title_writer") in ("claude", "api"):  # an AI looks at 3 frames of the moment: skips non-GTA / nothing-happening moments, tells the title writer what happens
        import ai_check
        try:
            src = json.loads((job / "candidates.json").read_text(encoding="utf-8"))["source"]
            res = None
            if CFG.get("ai_watch", False) and ai_check.watch_ok(CFG):  # the AI watches a small preview (picture + sound) of the final cut window
                res = ai_check.watch(CFG, src, c["start"], c["end"], c.get("text"))
                log(f"  AI watched the preview: {res['score']:g}/10 {'(reacts)' if res.get('reacts') else ''}" if res and res.get("ok") is not None else "  AI preview not possible: using 3 frames")
            if not res or res.get("ok") is None:
                res = ai_check.analyze(CFG, src, c["start"], c["end"], CHAT_HINT, c.get("text"))
        except Exception as e:
            res = {"ok": None, "reason": str(e)[:80], "what": ""}
        if res["ok"] is False and res.get("gta") and (win_score or 0) >= 7 and not ai_check.MUNDANE.search(res.get("what", "")):  # frames are a weak judge: a clearly strong bit (7+) is not vetoed by a quiet moment in them
            log(f"  AI frame check rated {res.get('score')}/10 but the bit scored {win_score:g}/10: keeping it"); res["ok"] = True
        if res["ok"] is False and (CFG.get("ai_check") or strict):
            log(f"  AI frame check: skipping ({res['reason']})"); return None
        if res.get("cutscene") and ORIGIN != "live" and (res.get("score") or 0) < 7:  # a cutscene can be funny, but a plain one is not a clip: it needs a clearly funny rating (7+)
            log(f"  AI frame check: plain cutscene rated {res.get('score')}/10, skipping ({res.get('what', '')[:70]})"); return None
        scene = res["what"] or None
        res_score = res.get("score") if res.get("ok") is not None else None
        log(f"  AI saw: {scene}" if scene else f"  AI frame look not possible ({res['reason']})")
    if not scene:  # no AI look (no AI configured, quota used up...): the no-AI visual check still rejects static / black / webcam-only moments
        try:
            import visual_check
            vr = visual_check.analyze(json.loads((job / "candidates.json").read_text(encoding="utf-8"))["source"], c["start"], c["end"])
            if vr["ok"] is False:
                log(f"  visual check: skipping ({vr['reason']}; motion {vr.get('motion')})"); return None
            log(f"  visual check: {vr['reason']} (motion {vr.get('motion')})")
        except Exception as e:
            log(f"  visual check not possible ({str(e)[:60]})")
    try:
        ns = snap_start(json.loads((job / "candidates.json").read_text(encoding="utf-8"))["source"], c["start"])
        if ns and abs(ns - c["start"]) > 0.1:
            log(f"  start moved {c['start']:.1f}s -> {ns:.1f}s to a scene cut")
            c = {**c, "start": ns}; cut_range = ["--start", f"{ns:.2f}", "--end", f"{c['end']:.2f}"]
    except Exception:
        pass
    ai = [x for x in (win_score, res_score, c.get("ai")) if x is not None]
    rank = (sum(ai) / len(ai) if ai else 5.0) + score * 0.3  # AI judgement first, the local viral score second
    return {"c": c, "cut_range": cut_range, "scene": scene, "rank": rank}


def try_candidate(v, score, job, n, c, creator, dry, memo=None):
    """Cut one moment and post it. Returns an exit code when done (posted / dry run), or None when this moment is not good enough.
    memo = the result of judge() when the caller already judged (and ranked) this moment."""
    jd = memo or judge(v, score, job, n, c, creator)
    if jd is None:
        return None
    c, cut_range, scene = jd["c"], jd["cut_range"], jd["scene"]
    progress(50, f"Cutting clip (score {score})")
    name = display_name(v, creator)
    q = quote_hook(c.get("text", ""))
    # wording: the quote is from the clip, but the tool cannot tell the streamer's voice from the game's audio, so never say "X said"
    hook = f'{name} reacts to {TP["name"]}: "{q}"' if q else f'{name} reacts to {TP["name"]}'
    seam = seam_title(q, name)
    blurb = ""
    if CFG.get("title_writer") in ("claude", "api"):  # titles only: Claude CLI or the user own API; any problem -> keep the quote titles above
        words = json.loads((job / "transcript.json").read_text(encoding="utf-8"))
        ai = ai_title.write(CFG, " ".join(w["w"] for w in words if c["start"] <= w["s"] < c["end"]), name, v["title"], scene, CHAT_HINT)
        if ai:
            hook, seam, blurb = ai["youtube_title"], ai["on_video"], ai["tiktok_line"]
            log(f"  AI titles: on-video '{seam}' | YouTube '{hook}' | description '{blurb}'")
        else:
            log("  AI titles unavailable (not signed in / no key / invalid answer): using no-AI titles")
            hook, seam, blurb = _noai(c, q, hook, seam, scene)
    if CFG.get("title_writer") not in ("claude", "api"):
        hook, seam, blurb = _noai(c, q, hook, seam, scene)
    seam = ""  # no text on the video (viewers can see what happens); the post title is just the short catchy line, never "X reacts to ..."
    hook = blurb or f'{TP["name"]} 😭'
    log(f"  title/description: {hook}")
    clip = None
    for layout in LAYOUTS:
        extra = ["--title", seam, "--title-style", "outline"] if (layout == "facecam" and seam) else []  # big caps title on the seam (webcam layout), only when there is a real one
        r = sh([PY, "scripts/cut.py", str(job), "--pick", str(n), "--layout", layout, "--hook", hook, *extra, *cut_range], timeout=1800)
        cp = job / "edits" / f"clip_{n}.mp4"
        chk = sh([PY, "scripts/check.py", str(cp)])
        ok = cp.exists() and "RESULT: PASS" in chk.stdout
        if ok and layout == "facecam":
            try:  # a detected "cam" covering most of the screen means detection failed (top panel would just repeat the game)
                box = [float(x) for x in json.loads(cp.with_suffix(".json").read_text(encoding="utf-8"))["cam"].split(":")]
                if box[2] > 0.5 or box[3] > 0.5:
                    log(f"  facecam box too large {box}; using the blurred layout instead"); continue
            except Exception:
                pass
            cov = face_coverage(cp)
            if cov < MIN_FACE_COVERAGE:  # the fixed webcam crop shows something else (game, blank scene) for part of the clip
                log(f"  the webcam panel shows a face in only {cov:.0%} of the clip (no webcam or scene switches): using the blurred layout instead"); continue
            log(f"  webcam shows a face in {cov:.0%} of the clip")
        if ok:
            clip = cp; log(f"  cut ok ({layout})"); break
        log(f"  cut/check failed with {layout}: " + (chk.stdout[-120:] or r.stderr[-120:]))
    if not clip:
        return None
    try:  # short catchy description: the AI one-liner goes into clip_N.json, make_meta.py uses it (no transcript text in descriptions); a stale meta file is dropped
        cj = clip.with_suffix(".json"); d0 = json.loads(cj.read_text(encoding="utf-8")); d0["blurb"] = blurb or ""
        d0.update({"source_id": v["id"], "origin": ORIGIN, "score": score, "creator": creator or "", "seconds": round(d0.get("end", 0) - d0.get("start", 0), 1)})
        cj.write_text(json.dumps(d0, ensure_ascii=False, indent=1), encoding="utf-8")
        clip.with_name(clip.name.replace("clip_", "meta_")).with_suffix(".json").unlink(missing_ok=True)
    except Exception:
        pass
    progress(65, "Clip checked")
    if dry:
        progress(100, f"Dry run: would post {clip.name}"); log(f"DRY RUN: {clip}"); return 0
    if not CFG.get("auto_post", False):  # "Post videos automatically" is OFF: the clip waits in the dashboard (Ready to post) for the user's Post button
        stem = queue_for_review(clip)
        progress(100, "Clip ready in the dashboard: press Post to publish it"); log(f"READY FOR REVIEW: {stem}")
        return 0
    with post_lock():  # one upload at a time (autorun and the live watcher share the one signed-in browser profile)
        r = sh([PY, "scripts/post_clip.py", str(clip)], timeout=3000)
    log(r.stdout[-400:] or r.stderr[-400:])
    return r.returncode


def try_videos(vids, st, sf, dry, base_pct, creator=None):
    """Analyse videos in order; for each, try its 3 best strong moments. Returns an exit code if it posted/dry-ran, else None."""
    for v in vids:
        progress(base_pct, f"Analysing: {v['channel']} - {v['title'][:50]}")
        log(f"  analysing {v['id']} ({v['duration'] // 60} min, {v['views']} views) {v['title'][:60]}")
        cands = strong_candidates(v)
        if not cands and yt_blocked():  # download blocked, not a bad video: keep it for later
            log("  YouTube is blocking downloads right now; nothing was judged"); return None
        if not cands and v.get("_dl_failed"):
            log("  could not fetch / analyse this one (technical problem): not marked as rejected"); continue
        if not cands:
            if v.get("custom"):
                log("  no strong moments in this link"); continue  # a link the user chose is never put on the rejected list
            log("  no strong moments; rejected"); st["rejected"].append(v["id"]); sf.write_text(json.dumps(st, indent=1), encoding="utf-8"); continue
        judged = []  # judge the best few moments first, then cut the best-ranked one (AI judgement + viral score), not just the first that passes
        for score, job, n, c in cands[: (4 if not v.get("custom") else 4 + 2 * (CUSTOM or {}).get("clips", 1))]:
            jd = judge(v, score, job, n, c, creator)
            if jd:
                judged.append((jd["rank"], score, job, n, jd))
        want, made = ((CUSTOM or {}).get("clips", 1) if v.get("custom") else 1), 0  # a normal run makes one clip per video; a link can ask for several
        for rank, score, job, n, jd in sorted(judged, key=lambda x: -x[0]):
            log(f"  best-ranked moment {n} (rank {rank:.1f})")
            code = try_candidate(v, score, job, n, jd["c"], creator, dry, memo=jd)
            if code is not None:
                made += 1
                if made >= want:
                    return code
        if made:
            return 0
        if v.get("custom"):
            log("  none of the moments in this link passed the checks"); continue
        log("  none of the top moments passed the checks; rejected")
        st["rejected"].append(v["id"]); sf.write_text(json.dumps(st, indent=1), encoding="utf-8")
    return None


def _custom(a, st, sf):
    """Clip the link the user typed into the dashboard (a YouTube video / Short, a Twitch VOD or clip): the normal pipeline on that one source."""
    global CUSTOM
    if not re.match(r"^https://(www\.|m\.|clips\.)?(youtube\.com|youtu\.be|twitch\.tv)/", a.url):
        progress(-1, "That link is not a YouTube or Twitch link"); log("custom link refused: not a YouTube / Twitch link"); return 3
    if re.search(r"youtu", a.url) and yt_blocked():
        progress(-1, "YouTube is asking Clippy to confirm it is not a bot: try again later"); log("custom link: YouTube block active"); return 0
    try:
        import update_ytdlp
        update_ytdlp.main(before_run=True)
    except BaseException as e:
        log(f"  yt-dlp update check skipped ({str(e)[:60]})")
    progress(5, "Reading the link")
    try:
        info = ytdlp_json(a.url, ["--no-playlist"])
    except Exception as e:
        progress(-1, "Could not read that link (private, removed, or not a video)"); log(f"custom link: could not read it ({str(e)[:120]})"); return 3
    if info.get("is_live"):
        progress(-1, "That is a live stream: a live stream can only be clipped after it ends"); log("custom link: live stream, refused"); return 3
    v = {"id": info.get("id") or "link", "title": info.get("title") or "(no title)", "channel": info.get("channel") or info.get("uploader") or "?",
         "duration": info.get("duration") or 0, "views": info.get("view_count") or 0, "url": a.url, "custom": True}
    CUSTOM = {"clips": max(1, min(5, a.clips)), "start": a.cstart, "len": a.clen}
    log(f"custom link: {v['title'][:70]} by {v['channel']} ({v['duration'] // 60} min); clips wanted: {CUSTOM['clips']}"
        + (f"; looking at minutes {a.cstart}-{a.cstart + (a.clen or 12)}" if a.cstart is not None else ""))
    code = try_videos([v], st, sf, a.dry, 25, None)
    if code is None:
        progress(100, "Done: no moment in that link was good enough (see the log)"); log("custom link: nothing good enough to clip")
        return 0
    return code


def configure(cfg):
    """Apply dashboard settings (clip length, layout, title writer) to this module; also used by live_watch.py."""
    global LIMITS, LAYOUTS, CFG, TP
    CFG = cfg
    TP = topic_mod.info(cfg)
    try:
        import ai_check, ai_title
        ai_check.TOPIC_NAME = ai_title.TOPIC_NAME = TP["name"]; ai_check.TOPIC_GTA = TP["gta"]
    except Exception:
        pass
    mx = max(10, min(60, int(cfg.get("clip_seconds", 30)))); LIMITS = (min(max(8, int(mx * 0.4)), 14), mx)  # short punchy moments stay short (14 s at most as the minimum); a moment that goes on may use all of clip_seconds
    LAYOUTS = ("blur",) if cfg.get("layout") == "blur" else ("facecam", "blur")  # facecam falls back to blur when no usable face/webcam


def main():
    import faulthandler
    faulthandler.enable()  # a hard crash of Python writes its stack to the log instead of vanishing
    pidf = R / "autorun.pid"; t_start = time.time()
    pidf.write_text(json.dumps({"pid": os.getpid(), "start": t_start}))  # the dashboard checks this: is the run alive, and since when
    code = "interrupted"
    try:
        code = _main()
        return code
    finally:  # always free the big downloaded source videos once clips exist (see cleanup.py)
        secs = int(time.time() - t_start)
        log(f"run ended (result {code}) after {secs // 60} min {secs % 60:02d} s")
        pidf.unlink(missing_ok=True)
        try:  # the last runs' lengths: the dashboard turns them into "a typical run takes about ..."
            hf = R / "run_history.json"; h = json.loads(hf.read_text(encoding="utf-8")) if hf.exists() else []
            h.append({"start": dt.datetime.fromtimestamp(t_start).isoformat(timespec="seconds"), "secs": secs, "result": str(code)})
            hf.write_text(json.dumps(h[-20:]), encoding="utf-8")
        except Exception:
            pass
        log(sh([PY, "scripts/cleanup.py"]).stdout.strip())


def _main():
    ap = argparse.ArgumentParser(); ap.add_argument("--force", action="store_true"); ap.add_argument("--dry", action="store_true")
    ap.add_argument("--trending-only", action="store_true", help="test: ignore the streamer list and use only trending sources")
    ap.add_argument("--creator", help="test: look only at this one streamer (no trending, the saved list is not changed)")
    ap.add_argument("--only", help="test: in the trending step look only at this one video / clip id")
    ap.add_argument("--relaxed", action="store_true", help="test: relaxed quality bar for this run only (the saved setting is not changed)")
    ap.add_argument("--url", help="clip this one link (a YouTube video / Short, a Twitch VOD or clip) instead of searching")
    ap.add_argument("--clips", type=int, default=1, help="with --url: how many clips to make from the link (1-5)")
    ap.add_argument("--cstart", type=int, help="with --url: only look at the part starting this many minutes into the video")
    ap.add_argument("--clen", type=int, help="with --url: how many minutes of it to look at (default 12)")
    a = ap.parse_args()
    cfg = json.loads((R / "autopost_config.json").read_text(encoding="utf-8"))
    sf = R / "autopost_state.json"
    st = json.loads(sf.read_text(encoding="utf-8")) if sf.exists() else {"used_video_ids": [], "posts": [], "consecutive_failures": 0}
    st.setdefault("rejected", [])
    if a.relaxed:
        cfg["quality"] = "relaxed"; cfg["ai_check"] = False  # in memory only: the AI frame check does not veto in this test run
    configure(cfg)
    if a.url:  # a link the user chose: no searching, no daily limits
        return _custom(a, st, sf)
    creators, trending_on = list(cfg.get("creators") or []), bool(cfg.get("use_trending_fallback") or cfg.get("trending_any"))
    if a.creator:
        creators, trending_on = [a.creator], False
    if a.trending_only:
        creators, trending_on = [], True
    if not creators and not trending_on:
        progress(-1, "No streamers in the list and trending is OFF: nothing to post. Add a streamer or turn trending on")
        log("BLOCKED: no streamers and trending is OFF"); return 3
    if cfg.get("live_watch") and not a.force:  # the live switch decides the source: ON = live streams only (live_watch.py), OFF = videos only
        progress(100, "Skipped: live mode is ON (live streams only)"); log("NOT RUN: live mode is ON, videos are not used"); return 0
    now = dt.datetime.now()
    if not a.force:
        ok, why, _ = tick.decide(cfg, st["posts"], now, (R / "PAUSED.txt").exists())
        if not ok:
            progress(100, f"Skipped: {why}"); log(f"NOT DUE: {why}"); return 0
    left = yt_blocked()
    if left:  # a cooldown is running: no searches, no downloads
        progress(100, f"Paused: YouTube asked to confirm 'not a bot', retrying in {left // 60} min"); log(f"YOUTUBE BLOCK: waiting {left // 60} more minutes before touching YouTube again"); return 0
    try:  # yt-dlp must be current before any YouTube work: check PyPI first (at most every 6 h; installs + self-tests + rolls back on failure)
        import update_ytdlp
        update_ytdlp.main(before_run=True)
    except BaseException as e:  # SystemExit included: an update problem must never stop a run
        log(f"  yt-dlp update check skipped ({str(e)[:60]})")
    progress(5, "Checked schedule")
    today = now.strftime("%Y-%m-%d")
    done_today = [norm(p.get("channel") or "") for p in st["posts"] if p.get("date") == today and not p.get("deleted")]
    maxc = int(cfg.get("max_per_creator_per_day", 1))
    skip_ch = {d for d in done_today if d and done_today.count(d) >= maxc}
    if cfg.get("creator_mode") == "random":
        random.shuffle(creators)
    if not a.creator:  # the --creator test flag ignores the daily per-creator limit
        creators = [c for c in creators if not any(norm(c) in d or d in norm(c) for d in skip_ch)]
    skip_ids = set(st["used_video_ids"]) | set(st["rejected"]) | review_ids()
    # 1) the user's list, in order
    for ci, creator in enumerate(creators[:10]):
        progress(10 + ci * 2, f"Looking for {creator}")
        log(f"creator: {creator}")
        vids = [v for v in search_creator(creator) if v["id"] not in skip_ids][:3]
        log(f"  {len(vids)} candidate videos")
        code = try_videos(vids, st, sf, a.dry, 25, creator)
        if code is not None:
            return code
    # 2) trending / popular, when allowed (also when the list is empty)
    if trending_on and cfg.get("creator_mode") != "fixed":
        progress(30, "List had nothing: checking what is trending")
        log("trending: looking at what is popular " + ("right now (any topic)" if TP["any"] else "for " + TP["name"]))
        skip_ch |= {norm(c) for c in creators}  # those were already tried above
        skip_ids = skip_ids - ({a.only} if a.only else set())
        vids = search_twitch_clips(skip_ids) + search_trending(skip_ch, skip_ids)  # viewer-made Twitch clips first (already picked by viewers)
        vids = [v for v in vids if v["id"] == a.only] if a.only else vids
        log(f"  {len(vids)} trending candidates: " + ", ".join(v["channel"] for v in vids))
        code = try_videos(vids, st, sf, a.dry, 35)
        if code is not None:
            return code
    progress(100, "Skipped: no strong moment found right now")
    log("no strong moment found; nothing posted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
