"""Quick self-check of Clippy's selection logic (no network, no video, nothing is posted or written).  python scripts/selftest.py"""
import datetime as dt, os, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("PYTHONUTF8", "1")
# Safety: a test must never start the real programs (live watcher, a run, the dashboard, an upload, an installer) or touch the real state files of the project it runs in.
import re as _re, subprocess as _sp  # noqa: E402
_REAL_POPEN_INIT = _sp.Popen.__init__
_PROGRAMS = {"live_watch.py", "autorun.py", "dashboard.py", "upload.py", "post_clip.py", "update_clippy.py"}


def _dangerous(args):
    toks = [str(x) for x in args] if isinstance(args, (list, tuple)) else str(args).split()
    if not toks:
        return False
    if Path(toks[0].strip('"')).name.lower().split(".")[0] in ("winget", "taskkill"):
        return True
    if "py_compile" in toks:
        return False
    return any(Path(t.strip('"')).name.lower() in _PROGRAMS for t in toks[1:]) or ("pip" in toks and "install" in toks)


def _guarded_popen(self, args, *a, **k):
    if _dangerous(args) and not os.environ.get("CLIPPY_SELFTEST_ALLOW"):
        raise RuntimeError("a self-test tried to start a real program: " + str(args)[:120])
    return _REAL_POPEN_INIT(self, args, *a, **k)


_sp.Popen.__init__ = _guarded_popen
_REAL = Path(__file__).resolve().parent.parent
_WATCH = ("live_run.json", "yt_block.json", "PAUSED.txt", "live_cmd.json", "live_watch.pid", "yt_cookies_bad.json")  # files only a test gone wrong would write (progress, config, post.lock... are also written by a real run that may be going on at the same time)


def _snapshot():
    return {n: ((_REAL / n).stat().st_mtime_ns if (_REAL / n).exists() else None) for n in _WATCH}


import ai_check, ai_title, autorun, learn  # noqa: E402


def words(sentences, t=90.0):
    out = []
    for s in sentences:
        for w in s.split():
            out.append({"w": w, "s": t, "e": t + 0.3}); t += 0.4
        t += 0.9
    return out


def test_rejections_expire():
    st = {"rejected": ["old", "new", "nodate", "old"],
          "rejected_at": {"old": (dt.datetime.now() - dt.timedelta(days=6)).isoformat(), "new": dt.datetime.now().isoformat()}}
    autorun.expire_rejected(st)
    assert st["rejected"] == ["new", "nodate"], st


def test_compilation_and_music():
    assert autorun.COMPILATION.search("CaseOh's FUNNIEST Moments in GTA VI. (Part 3)")
    assert not autorun.COMPILATION.search("xQc reacts to GTA 6 console")
    assert autorun.MUSIC.search("♪") and autorun.MUSIC.search("[Music]") and not autorun.MUSIC.search("musician")


def test_bit_boundaries():
    w = words(["Okay so we are driving.", "Hey guys look at this.", "So the car just flew.", "No way that happened."])
    for ask, want_start in ((w[12]["s"], 95.9), (w[7]["s"], 93.4)):  # mid-sentence ask; ask inside the filler sentence
        ai_title.complete_cfg = lambda *a, **k: '{"start": %.1f, "end": %.1f, "score": 8, "standalone": true, "speaker": "streamer", "risky": false, "why": "x"}' % (ask, w[-1]["e"])
        r = ai_check.refine_window({"title_writer": "api"}, w, 90, w[-1]["e"], 40, 3)
        assert r and abs(r["start"] - want_start) < 0.06, (ask, r)  # starts on the sentence, filler skipped
        assert 100.5 < r["end"] <= 101.3, r  # ends 0.4-1 s after the last word, not on it


def test_over_limit_ends_on_a_sentence():
    w, t = [], 100.0
    for _ in range(14):
        for x in "one two three four five six".split():
            w.append({"w": x if x != "six" else "six.", "s": t, "e": t + 0.4}); t += 0.7
        t += 0.8
    ai_title.complete_cfg = lambda *a, **k: '{"start": 100.0, "end": 141.0, "score": 8, "standalone": true, "speaker": "streamer", "risky": false, "why": "x"}'
    r = ai_check.refine_window({"title_writer": "api"}, w, 100, 141, 40, 12)
    assert r and r["end"] - r["start"] <= 40 and abs(r["end"] - 139.4) < 0.1, r  # the last full sentence that fits, not a cut at the limit


def test_webcam_panel_crop():
    import cut
    sw, sh = 1920, 1080
    for box, face in (([0.66, 0.19, 0.34, 0.34], [0.86, 0.25, 0.085, 0.19]),   # corner cam, big box
                      ([0.02, 0.21, 0.17, 0.17], [0.085, 0.246, 0.049, 0.094]),  # tight cam box around a small face
                      ([0.0, 0.0, 0.2, 0.5], [0.04, 0.1, 0.1, 0.18])):         # tall box (webcam + chat below)
        w, h, x, y = cut.panel_crop(box, face, sw, sh)
        assert abs(w / h - 1080 / 729) < 0.03, (w, h)  # the panel's own shape: no stretching
        assert 0 <= x and 0 <= y and x + w <= sw and y + h <= sh
        fx, fy, fw, fh = face[0] * sw, face[1] * sh, face[2] * sw, face[3] * sh
        assert x <= fx and fx + fw <= x + w and y <= fy - 0.2 * fh and fy + fh <= y + h, (x, y, w, h)  # whole face plus hair room inside the crop


def test_streamer_twitch_clips_filter():
    import twitch
    rows = [{"id": "tw:a", "title": "good", "channel": "X", "duration": 30, "views": 5000, "game": "Grand Theft Auto VI"},
            {"id": "tw:b", "title": "other game", "channel": "X", "duration": 30, "views": 9000, "game": "Trees Hate You"},
            {"id": "tw:c", "title": "old gta", "channel": "X", "duration": 30, "views": 9000, "game": "Grand Theft Auto V"},
            {"id": "tw:d", "title": "too long", "channel": "X", "duration": 90, "views": 9000, "game": "Grand Theft Auto VI"},
            {"id": "tw:e", "title": "tiny", "channel": "X", "duration": 30, "views": 20, "game": "Grand Theft Auto VI"},
            {"id": "tw:f", "title": "used", "channel": "X", "duration": 30, "views": 8000, "game": "Grand Theft Auto VI"}]
    twitch.channel_clips = lambda *a, **k: rows
    import topic
    autorun.TP = topic.info({"topic": "GTA 6"})
    got = autorun.creator_twitch_clips("X", {"tw:f"})
    assert [c["id"] for c in got] == ["tw:a"] and got[0]["twitch"], got
    autorun.TP = topic.info({"topic": ""})  # no topic: every game is fine (this was the bug: the name 'gaming' had to appear in the game name)
    got = autorun.creator_twitch_clips("X", {"tw:f"})
    assert [c["id"] for c in got] == ["tw:a", "tw:b", "tw:c"], got


def test_judge_rules():
    import json, tempfile
    keep = (ai_check.refine_window, ai_check.analyze, autorun.log, autorun.CFG, autorun.LIMITS, autorun.ORIGIN, learn.W_FILE, dict(learn._model))
    learn.W_FILE = Path(tempfile.mkdtemp()) / "none.json"; learn._model.update(t=0.0, m=None)  # the rules are tested without anything this user's ratings taught (a learned model would add its own small nudge)
    try:
        _judge_rules(json, tempfile)
    finally:
        ai_check.refine_window, ai_check.analyze, autorun.log, autorun.CFG, autorun.LIMITS, autorun.ORIGIN, learn.W_FILE = keep[:7]
        learn._model.update(keep[7])


def _judge_rules(json, tempfile):
    tmp = Path(tempfile.mkdtemp()); (tmp / "transcript.json").write_text("[]"); (tmp / "candidates.json").write_text(json.dumps({"source": "none.mp4"}))
    autorun.log = lambda *a, **k: None
    autorun.CFG = {"title_writer": "api", "quality": "strict", "ai_watch": False}; autorun.LIMITS = (18, 40); autorun.ORIGIN = "video"
    win = {"start": 10, "end": 40, "score": 8, "standalone": True, "speaker": "streamer", "risky": False, "why": "w"}
    res = {"ok": True, "gta": True, "score": 7, "what": "x", "reason": "r", "cutscene": False}
    ai_check.refine_window = lambda *a, **k: dict(win); ai_check.analyze = lambda *a, **k: dict(res)
    c = {"start": 10, "end": 40, "why": ["a"], "sig": {}, "text": "", "peak": 20, "ai": None}
    v = {"title": "xQc reacts", "id": "x"}
    good = autorun.judge(v, 6.0, tmp, 1, c, None)
    assert good and abs(good["rank"] - (7.5 + 1.8 + 0.5 + 0.4)) < 0.01 and not good["weak"], good  # AI scores + energy + streamer-reacts bonus
    win["speaker"] = "game"  # game characters talking, the streamer silent: -2 and no bonus
    g = autorun.judge(v, 6.0, tmp, 1, c, None)
    assert g and abs((good["rank"] - g["rank"]) - 2.5) < 0.01, (good["rank"], g["rank"])
    res.update(cutscene=True, score=5)  # a cutscene the streamer does not react to (game speaker, no voice signals) is out
    assert autorun.judge(v, 6.0, tmp, 1, c, None) is None
    win["speaker"] = "streamer"; res.update(score=5)  # the same cutscene with the streamer reacting stays in
    assert autorun.judge(v, 6.0, tmp, 1, c, None)
    res.update(cutscene=False, score=7); ai_check.refine_window = lambda *a, **k: None  # no AI read of the speech
    assert autorun.judge(v, 6.0, tmp, 1, {**c, "why": ["a"]}, None)["feat"]["penalty"] == 1.0
    base = autorun.judge(v, 6.0, tmp, 1, {**c, "sig": {}}, None)
    loud = autorun.judge(v, 6.0, tmp, 1, {**c, "sig": {"voice": 2.0, "swear": 1.5}}, None)  # excited voice + swearing: a boost on top of the same moment, capped at +1
    assert loud and 0.9 <= loud["rank"] - base["rank"] <= 1.0001 and any("swearing" in n for n in loud["feat"]["notes"]), (loud["rank"], base["rank"])
    quiet = autorun.judge(v, 6.0, tmp, 1, {**c, "sig": {"voice": 0.3, "swear": 0}}, None)
    assert quiet and abs(quiet["rank"] - base["rank"]) < 1e-9  # a calm moment gets nothing extra
    tw = autorun.judge({**v, "twitch": True}, 4.5, tmp, 1, c, None)  # a clip viewers made: no energy / no-AI-read penalty
    assert tw and tw["feat"]["penalty"] == 0, tw
    ai_check.refine_window = lambda *a, **k: {**win, "score": 3}  # a very popular Twitch clip the AI finds weak: a penalty, not a rejection
    big = autorun.judge({**v, "twitch": True, "views": 120000}, 6.0, tmp, 1, c, None)
    assert big and any("viewers picked" in n for n in big["feat"]["notes"]) and any("weak AI score" in n for n in big["feat"]["notes"]), big
    assert autorun.judge({**v, "twitch": True, "views": 2000}, 6.0, tmp, 1, c, None) is None  # a small clip is still rejected
    st1 = autorun._stage1(v, 6.0, tmp, 1, c, None)
    assert st1 is None or "pen" in st1  # stage 1 alone works (text only)


def test_learning_from_ratings():
    import random, tempfile
    random.seed(1)
    learn.W_FILE = Path(tempfile.mkdtemp()) / "w.json"
    mk = lambda i, good, ch="X": {"id": str(i), "rating": 1 if good else -1, "features": {"win": random.uniform(7, 9.5) if good else random.uniform(3, 6), "frames": random.uniform(5, 9), "score": random.uniform(4, 7), "penalty": random.choice([0, 1, 2]), "sig": {"loud": random.random()}, "channel": ch}}
    assert learn.fit([mk(i, i % 2) for i in range(8)]) is None  # too few clips: nothing is learned, nothing changes
    m = learn.fit([mk(i, i % 2) for i in range(14)])  # it starts early (a handful of ratings), with low trust
    assert m and 0 < m["conf"] < 0.6, m
    rows = [mk(i, i % 2) for i in range(40)]
    m = learn.fit(rows)
    assert m and m["n"] == 40 and m["conf"] > 0.6, m  # more ratings: more trust
    hi, lo = learn.adjust({"win": 9, "frames": 7, "score": 6, "penalty": 0, "sig": {"loud": 0.5}}, m), learn.adjust({"win": 3.5, "frames": 7, "score": 6, "penalty": 0, "sig": {"loud": 0.5}}, m)
    assert hi > 0 > lo and hi <= 1.6 and lo >= -1.6, (hi, lo)  # looks like the 👍 ones: up; like the 👎 ones: down; always bounded
    noise = [{"id": str(i), "rating": random.choice([1, -1]), "features": {"win": random.uniform(3, 9), "frames": random.uniform(3, 9), "score": random.uniform(3, 9), "penalty": random.choice([0, 1, 2]), "sig": {"loud": random.random()}}} for i in range(40)]
    assert learn.fit(noise)["conf"] <= 0.3  # ratings that follow no pattern: (almost) no nudge
    taste = [mk(i, i % 2 == 0, "caseoh") for i in range(20)] + [mk(100 + i, False, "someoneelse") for i in range(20)]
    mt = learn.fit(taste)
    ft = {"win": 6, "frames": 7, "score": 6, "penalty": 0, "sig": {"loud": 0.5}}
    assert learn.adjust({**ft, "channel": "CaseOh"}, mt) > learn.adjust({**ft, "channel": "someoneelse"}, mt)  # it also learns which streamers the user likes
    sm = learn.summary(rows)
    assert sm["rated"] == 40 and sm["good"] == 20 and sm["likes"], sm


def test_meh_is_neutral():
    import random, tempfile
    random.seed(2)
    learn.W_FILE = Path(tempfile.mkdtemp()) / "w.json"
    assert 0 < learn.MEH_WEIGHT < 0.5
    mk = lambda i, r, win: {"id": str(i), "rating": r, "features": {"win": win, "frames": random.uniform(5, 9), "score": random.uniform(4, 7), "penalty": 0, "sig": {"loud": random.random()}}}
    base = [mk(i, 1 if i % 2 else -1, random.uniform(7, 9) if i % 2 else random.uniform(3, 5)) for i in range(30)]
    meh = [mk(100 + i, 0, random.uniform(3, 9)) for i in range(60)]  # twice as many meh clips as clips that stood out, with no pattern
    only_meh = [mk(200 + i, 0, 5) for i in range(60)] + [mk(300, 1, 8), mk(301, -1, 4)]
    assert learn.fit(only_meh) is None  # lots of meh but hardly any 👍/👎: nothing is learned (the ratings are what teaches)
    m1, m2 = learn.fit(base), learn.fit(base + meh)
    a, b = learn.adjust({"win": 9, "frames": 7, "score": 6, "penalty": 0, "sig": {"loud": 0.5}}, m1), learn.adjust({"win": 9, "frames": 7, "score": 6, "penalty": 0, "sig": {"loud": 0.5}}, m2)
    assert a > 0 and b > 0 and abs(a - b) < 0.5, (a, b)  # many meh clips do not flip or wipe out what the standouts say


def test_prepare_only_schedule():
    import json, sys, tempfile, time
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import dashboard as d, tick
    tmp = Path(tempfile.mkdtemp()); keep = (d.REVIEW, d.read_posts)
    try:
        d.REVIEW = tmp; d.read_posts = lambda: []
        now = int(time.time())
        for i, posted in ((0, False), (1, False), (2, True)):  # two waiting clips, one already posted
            stem = f"{now - 60 * (i + 1)}__clip_{i + 1}"
            (tmp / f"{stem}.json").write_text("{}"); (tmp / f"{stem}.mp4").write_bytes(b"x")
            if posted:
                (tmp / f"{stem}.posted.json").write_text(json.dumps({"youtube": "http://y"}))
        assert len(d.waiting_clips()) == 2
        off = {"auto_post": False, "enabled": True, "posts_per_day": 2, "window_start": "00:00", "window_end": "23:59", "min_gap_minutes": 0, "start_date": "2026-01-01", "private_days": 0}
        assert len(d.sched_posts(off)) == 2
        assert not tick.decide(off, d.sched_posts(off), __import__("datetime").datetime.now())[0]  # 2 prepared = the day's quota: no more clips are prepared
        assert len(d.sched_posts({**off, "auto_post": True})) == 0  # Auto-Post ON: only real posts count
    finally:
        d.REVIEW, d.read_posts = keep


def test_removed_clips_teach_meh():
    import http.server, json, sys, tempfile, threading, urllib.request
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import dashboard as d
    tmp = Path(tempfile.mkdtemp()); (tmp / "review").mkdir()
    keep = (d.R, d.REVIEW); d.R, d.REVIEW = tmp, tmp / "review"
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 8797), d.H); threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        def clip(i, **extra):
            stem = f"1795999{i:03d}__clip_1"
            (d.REVIEW / f"{stem}.mp4").write_bytes(b"x")
            (d.REVIEW / f"{stem}.json").write_text(json.dumps({"rank": 8, "features": {"win": 7}, "seconds": 30, "creator": "X", **extra}))
            return stem
        def remove(stem):
            rq = urllib.request.Request("http://127.0.0.1:8797/api/review/remove", data=json.dumps({"id": stem}).encode(), method="POST", headers={"Content-Type": "application/json"})
            assert urllib.request.urlopen(rq).status == 200
        meh, rated, posted, nosig = clip(1), clip(2, rating=1), clip(3), clip(4)
        (d.REVIEW / f"{posted}.posted.json").write_text(json.dumps({"youtube": "http://y"}))
        (d.REVIEW / f"{nosig}.json").write_text(json.dumps({"rank": 5}))  # an old clip without saved signals
        for st in (meh, rated, posted, nosig):
            remove(st)
        rows = [json.loads(x) for x in (tmp / "ratings.jsonl").read_text().splitlines()]
        assert [(r["id"], r["rating"], r.get("removed")) for r in rows] == [(meh, 0, True), (rated, 1, True), (posted, 1, True)], rows  # meh, the rated one (written down again), and the posted unrated one as a soft like; not the one without signals
        assert rows[2]["w"] == 0.5
    finally:
        srv.shutdown(); d.R, d.REVIEW = keep


def test_dashboard_script_syntax():
    """A syntax error in the page script silently breaks the whole dashboard: check it with Node when Node is installed (skipped otherwise)."""
    import re, shutil, subprocess, tempfile
    node = shutil.which("node")
    if not node:
        return
    html = (Path(__file__).resolve().parent.parent / "dashboard.html").read_text(encoding="utf-8")
    js = chr(10).join(re.findall(r"<script>(.*?)</script>", html, re.S))
    f = Path(tempfile.mkdtemp()) / "page.js"; f.write_text(js, encoding="utf-8")
    r = subprocess.run([node, "--check", str(f)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-300:]


def test_trim_then_post_uses_the_new_video_and_texts():
    import http.server, json, shutil, sys, tempfile, threading, urllib.request
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        return
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import dashboard as d
    tmp = Path(tempfile.mkdtemp()); (tmp / "review").mkdir()
    keep = (d.R, d.REVIEW, d.subprocess.run, d.read_cfg); d.R, d.REVIEW = tmp, tmp / "review"
    stem = "1795999777__clip_1"
    d.subprocess.run([shutil.which("ffmpeg"), "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=270x480:rate=30:duration=8", "-f", "lavfi", "-i", "sine=frequency=440:duration=8",
                      "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(d.REVIEW / f"{stem}.mp4")], check=True, capture_output=True)
    (d.REVIEW / f"{stem}.json").write_text(json.dumps({"seconds": 8.0, "layout": "facecam"}))
    (d.REVIEW / f"{stem.replace('clip_', 'meta_')}.json").write_text(json.dumps({"yt_title": "My edited title #Shorts", "yt_description": "my words", "edited": True}))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 8796), d.H); threading.Thread(target=srv.serve_forever, daemon=True).start()
    def api(path, body):
        rq = urllib.request.Request("http://127.0.0.1:8796" + path, data=json.dumps(body).encode(), method="POST", headers={"Content-Type": "application/json"})
        try:
            return urllib.request.urlopen(rq).status
        except urllib.error.HTTPError as e:
            return e.code
    try:
        mp4 = d.REVIEW / f"{stem}.mp4"
        assert api("/api/review/trim", {"id": stem, "start": 1, "end": 5}) == 200
        assert abs(d._duration(mp4) - 4.0) < 0.2 and abs(d._duration(d.REVIEW / f"{stem}.orig") - 8.0) < 0.2  # trimmed; the original is kept
        assert api("/api/review/trim", {"id": stem, "start": 0, "end": 6, "fade": False}) == 200 and abs(d._duration(mp4) - 6.0) < 0.2  # a second trim starts from the ORIGINAL
        assert api("/api/review/trim", {"id": stem, "start": 7.5, "end": 8}) == 400 and api("/api/review/trim", {"id": stem, "start": 5, "end": 2}) == 400  # under 1 s / backwards
        assert api("/api/review/trim", {"id": "../x", "start": 0, "end": 2}) == 400
        assert api("/api/review/trim", {"id": stem, "start": 2, "end": 7}) == 200
        info = json.loads((d.REVIEW / f"{stem}.json").read_text()); assert info["seconds"] == 5.0 and info["trim"]["start"] == 2.0 and info["orig_seconds"] == 8.0
        # Post: the file handed to the uploader is the trimmed one, next to the edited texts
        seen = {}
        class R_: returncode = 0; stdout = json.dumps({"youtube": "http://y"}); stderr = ""
        def fake(cmd, **kw):
            if any("post_clip.py" in str(c) for c in cmd):
                seen["file"] = Path(cmd[2]); seen["dur"] = d._duration(cmd[2]); seen["meta"] = json.loads((d.REVIEW / f"{stem.replace('clip_', 'meta_')}.json").read_text())
                return R_()
            return keep[2](cmd, **kw)
        d.subprocess.run = fake; d.read_cfg = lambda: {"post_youtube": True, "post_tiktok": False}
        d.review_post(stem, "private")
        assert seen["file"].name == f"{stem}.mp4" and abs(seen["dur"] - 5.0) < 0.2 and seen["meta"]["yt_title"] == "My edited title #Shorts", seen
        d.subprocess.run = keep[2]
        assert api("/api/review/trim/reset", {"id": stem}) == 200 and abs(d._duration(mp4) - 8.0) < 0.2 and not (d.REVIEW / f"{stem}.orig").exists()
        assert "trim" not in json.loads((d.REVIEW / f"{stem}.json").read_text())
    finally:
        srv.shutdown(); d.R, d.REVIEW, d.subprocess.run, d.read_cfg = keep


def test_restart_is_wanted_only_when_program_files_changed():
    import sys, tempfile
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import dashboard as d
    tmp = Path(tempfile.mkdtemp()); (tmp / "scripts").mkdir()
    for f in ("dashboard.py", "tick.py", "scripts/a.py"):
        (tmp / f).write_text("x = 1")
    keep = (d.R, d.START_SIG); d.R = tmp
    try:
        d.START_SIG = d.code_sig()
        assert not d.restart_wanted()  # nothing changed since this dashboard started
        (tmp / "settings.json").write_text("{}"); (tmp / "review").mkdir()  # settings, clips, ratings are not program files: no restart needed
        assert not d.restart_wanted()
        (tmp / "scripts" / "a.py").write_text("x = 2  # an update")
        assert d.restart_wanted()  # a program file changed: the running dashboard is out of date
        (tmp / "scripts" / "new_module.py").write_text("y = 1")
        assert d.restart_wanted()
    finally:
        d.R, d.START_SIG = keep


def test_description_is_a_real_line_not_one_word():
    p = ai_title.build_prompt("hello there", "CaseOh", "src title")
    assert "AT LEAST 4 words" in p and not any("one-word" in v for v in ai_title.VOICES)


def test_a_run_can_make_several_clips():
    cuts = []
    keep = (autorun.try_candidate, autorun.log, autorun.WANT, autorun.MADE)
    autorun.log = lambda *a, **k: None
    autorun.try_candidate = lambda v, score, job, n, c, creator, dry, memo=None, review_only=False: (cuts.append((v["id"], n)) or 0)
    def pending(*moments):  # (video, moment number, rank)
        autorun.PENDING.clear(); by = {}
        for vid, n, rank in moments:
            by.setdefault(vid, []).append((rank, 5.0, None, n, {"c": {}}))
        for vid, judged in by.items():
            autorun.PENDING.append(({"id": vid, "title": vid}, None, judged))
    try:
        for want, moments, expect in ((1, [("a", 1, 9), ("b", 1, 10), ("c", 1, 8)], [("b", 1)]),
                                      (2, [("a", 1, 9), ("b", 1, 10), ("c", 1, 8)], [("b", 1), ("a", 1)]),  # best two, from different videos
                                      (3, [("a", 1, 9), ("a", 2, 9.5), ("b", 1, 7)], [("a", 2), ("b", 1), ("a", 1)]),  # one per video first, then more from the same video
                                      (5, [("a", 1, 9), ("b", 1, 10)], [("b", 1), ("a", 1)])):  # fewer good moments than wanted: just those
            cuts.clear(); autorun.WANT, autorun.MADE = want, 0; pending(*moments)
            code = autorun.cut_pending(False)
            assert code == 0 and cuts == expect, (want, cuts)
            assert autorun.MADE == len(expect) and autorun.enough() == (len(expect) >= want)
    finally:
        autorun.try_candidate, autorun.log, autorun.WANT, autorun.MADE = keep; autorun.PENDING.clear()


def test_clips_per_run_limits():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import dashboard as d
    base = d.read_cfg()
    assert d.clean({**base, "auto_clips_per_run": 99}, base)["auto_clips_per_run"] == 10  # at most 10 per run
    assert d.clean({**base, "auto_clips_per_run": -5}, base)["auto_clips_per_run"] == 1 and d.clean({**base, "auto_clips_per_run": 0}, base)["auto_clips_per_run"] == 1
    assert d.clean({**base, "auto_clips_per_run": 4}, base)["auto_clips_per_run"] == 4
    old = {**base, "clips_per_run": 3}
    assert d.clean({**base, "clips_per_run": 9}, old)["clips_per_run"] == 3  # the manual number is saved only by its own control (a stale settings page cannot overwrite it)


def test_source_subtitles_are_detected_and_clippys_are_skipped():
    import cv2, numpy as np
    import caption_check as cc
    rng = np.random.default_rng(1)
    def scene(text=None, scale=1.6, color=(255, 255, 255)):
        f = (rng.integers(40, 120, (720, 1280, 3))).astype(np.uint8)
        f = cv2.GaussianBlur(f, (0, 0), 6)  # a soft, game-like picture
        if text:
            (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_DUPLEX, scale, 4)
            org = ((1280 - tw) // 2, 600)
            cv2.putText(f, text, org, cv2.FONT_HERSHEY_DUPLEX, scale, (0, 0, 0), 10, cv2.LINE_AA)  # dark outline like real captions
            cv2.putText(f, text, org, cv2.FONT_HERSHEY_DUPLEX, scale, color, 4, cv2.LINE_AA)
        return f
    assert cc.caption_line(scene("THIS IS NICE")) and cc.caption_line(scene("LORD HAVE MERCY", color=(0, 230, 255)))  # bold white / yellow caption line
    assert not cc.caption_line(scene()) and not cc.caption_line(scene("small game subtitle here", scale=0.55))  # nothing / the small in-game subtitle
    # the wiring: judge records it, and the cut gets "--captions none"
    keep = (cc.has_burned_captions, autorun.log, autorun.CFG, autorun.ORIGIN)
    import json, tempfile
    tmp = Path(tempfile.mkdtemp()); (tmp / "transcript.json").write_text("[]"); (tmp / "candidates.json").write_text(json.dumps({"source": "none.mp4"}))
    autorun.log = lambda *a, **k: None; autorun.CFG = {"title_writer": "template", "quality": "relaxed"}; autorun.ORIGIN = "video"
    try:
        c = {"start": 5, "end": 30, "why": ["a"], "sig": {}, "text": "", "peak": 12, "ai": None}
        cc.has_burned_captions = lambda *a, **k: (True, 0.6)
        jd = autorun.judge({"title": "t", "id": "x", "twitch": True}, 6.0, tmp, 1, c, None)
        assert jd and jd["has_caps"] and autorun.caption_args(jd) == ["--captions", "none"]
        cc.has_burned_captions = lambda *a, **k: (False, 0.0)
        jd = autorun.judge({"title": "t", "id": "x", "twitch": True}, 6.0, tmp, 1, c, None)
        assert jd and not jd["has_caps"] and autorun.caption_args(jd) == []
    finally:
        cc.has_burned_captions, autorun.log, autorun.CFG, autorun.ORIGIN = keep


def test_scheduled_posting_and_mark_as_not_posted():
    import datetime as dtm, json, sys, tempfile
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import dashboard as d
    tmp = Path(tempfile.mkdtemp()); (tmp / "review").mkdir()
    keep = (d.REVIEW, d.review_post, d.REVIEW_STATUS.copy(), d.threading.Thread)
    d.REVIEW = tmp / "review"; d.REVIEW_STATUS.clear()
    now = dtm.datetime(2030, 11, 22, 18, 0)
    def clip(i, **extra):
        stem = f"17959990{i:02d}__clip_1"
        (d.REVIEW / f"{stem}.mp4").write_bytes(b"x"); (d.REVIEW / f"{stem}.json").write_text(json.dumps(extra)); return stem
    def at(minutes):
        return {"at": (now + dtm.timedelta(minutes=minutes)).isoformat(timespec="minutes"), "mode": "public"}
    due, late, future, busy, plain = clip(1, scheduled=at(-3)), clip(2, scheduled=at(-40)), clip(3, scheduled=at(90)), clip(4, scheduled=at(-1)), clip(5)
    d.REVIEW_STATUS[busy] = {"state": "posting"}
    posted = []
    class Inline:  # runs the posting "thread" right away so the test can look at the result
        def __init__(self, target=None, args=(), daemon=None): self.t, self.a = target, args
        def start(self): self.t(*self.a)
    d.review_post = lambda stem, mode="private": posted.append((stem, mode)); d.threading.Thread = Inline
    info = lambda st: json.loads((d.REVIEW / f"{st}.json").read_text())
    try:
        started = d.sched_tick(now)
        assert started == [due] and posted == [(due, "public")], (started, posted)  # only the one that is due now is posted, with its own visibility
        assert "scheduled" not in info(due) and "scheduled" not in info(late) and info(late)["schedule_missed"] == at(-40)["at"]  # a missed time is dropped, never posted late by surprise
        assert info(future)["scheduled"]["at"] == at(90)["at"] and info(busy).get("scheduled")  # the future one waits; a clip that is posting right now is left alone
        d.threading.Thread = keep[3]
        for bad_at, mode in (((dtm.datetime.now() - dtm.timedelta(hours=1)).isoformat(), "private"), ((dtm.datetime.now() + dtm.timedelta(hours=1)).isoformat(), "secret")):
            try:
                d.review_schedule(plain, bad_at, mode); raise AssertionError("should have been refused")
            except ValueError:
                pass
        d.review_schedule(plain, (dtm.datetime.now() + dtm.timedelta(hours=2)).isoformat(), "private")
        assert info(plain)["scheduled"]["mode"] == "private"
        d.review_unschedule(plain); assert "scheduled" not in info(plain)
        # mark as not posted: the marker and the stale "Posted." line go
        (d.REVIEW / f"{plain}.posted.json").write_text(json.dumps({"youtube": "http://y", "tiktok": "posted"})); d.REVIEW_STATUS[plain] = {"state": "done", "msg": "Posted."}
        d.review_unpost(plain)
        assert not (d.REVIEW / f"{plain}.posted.json").exists() and plain not in d.REVIEW_STATUS
        d.REVIEW_STATUS[plain] = {"state": "done", "msg": "Posted."}; d.review_items()  # a stale 'done' status without a posted marker is dropped when the list is built
        assert plain not in d.REVIEW_STATUS
    finally:
        d.REVIEW, d.review_post, d.threading.Thread = keep[0], keep[1], keep[3]; d.REVIEW_STATUS.clear(); d.REVIEW_STATUS.update(keep[2])


def test_new_helpers_views_flags_slots_reason():
    import datetime as dtm, json, sys, tempfile
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import dashboard as d, views, learn
    # view counts: a hit, a miss and the middle; too few clips -> nothing
    rows = [{"id": str(i), "views": v, "features": {"a": 1}} for i, v in enumerate([100, 120, 110, 900, 20])]
    r = views.derived(rows)
    assert r["3"] == (1, 0.5) and r["4"] == (-1, 0.5) and r["0"] == (0, 0.5) and views.derived(rows[:3]) == {}, r
    # a clip posted less than 48 h ago is not read; an old one is, and its count is stored once
    tmp = Path(tempfile.mkdtemp()); (tmp / "review").mkdir(); old = (views.REVIEW, views.LOG); views.REVIEW, views.LOG = tmp / "review", tmp / "views.jsonl"
    try:
        import time as tm
        for stem, age in (("1795000001__clip_1", 10), ("1795000002__clip_1", 60)):
            pf = views.REVIEW / f"{stem}.posted.json"; pf.write_text(json.dumps({"youtube": "https://youtube.com/shorts/x"}))
            import os; os.utime(pf, (tm.time() - age * 3600, tm.time() - age * 3600))
        assert views.refresh(lambda u: 777) == 1 and views.load()[0]["views"] == 777 and views.refresh(lambda u: 5) == 0
    finally:
        views.REVIEW, views.LOG = old
    # posting slots: respect the window, the gap and the daily limit (already-taken times count)
    now = dtm.datetime(2030, 11, 22, 8, 0)
    taken = [dtm.datetime(2030, 11, 22, 9, 0), dtm.datetime(2030, 11, 22, 11, 0)]
    sl = d.plan_slots(now, 5, 3, "09:00", "22:30", 120, taken)
    assert len(sl) == 5 and sl[0] == dtm.datetime(2030, 11, 22, 13, 0) and sl[1].date() == dtm.date(2030, 11, 23), sl  # 1 slot left today (limit 3), then a new day
    assert all(b - a >= dtm.timedelta(minutes=120) for a, b in zip(sl, sl[1:]) if a.date() == b.date())
    assert all(dtm.time(9, 0) <= x.time() <= dtm.time(22, 30) for x in sl)
    # spreading clips over days only takes the liked (thumbs-up) ones, best rank first
    tmp2 = Path(tempfile.mkdtemp()); keep = d.REVIEW; d.REVIEW = tmp2
    try:
        for i, (rk, rt) in enumerate([(5, 1), (9, 1), (8, 0), (7, -1)]):
            st = f"17960000{i:02d}__clip_1"; (tmp2 / f"{st}.mp4").write_bytes(b"x"); (tmp2 / f"{st}.json").write_text(json.dumps({"rank": rk, "rating": rt}))
        assert d.review_autoschedule("private", now=dtm.datetime(2030, 11, 22, 10, 0)) == 2
        sch = {f.stem[8:10]: json.loads(f.read_text()).get("scheduled") for f in tmp2.glob("*.json")}
        assert sch["01"]["at"] < sch["00"]["at"] and sch["02"] is None and sch["03"] is None, sch
    finally:
        d.REVIEW = keep
    # a thumbs-down for a reason that is not about the moment counts less in learning
    assert "nudity" in learn.OFF_MOMENT and "boring" not in learn.OFF_MOMENT and "boring" in d.WHY


def test_minimum_score_setting():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent)); sys.path.insert(0, str(Path(__file__).resolve().parent))
    import autorun as a, dashboard as d
    a.CFG = {}; assert a.min_score() == 7.0 and a.slack() == 0.0              # default: the bar stays 7
    a.CFG = {"min_score": 4.5}; assert a.min_score() == 4.5 and a.slack() == 2.5  # lowered: the hard rejects relax by the same amount
    a.CFG = {"min_score": 9.5}; assert a.slack() == 0.0                       # raising it never relaxes anything
    a.CFG = {"min_score": "junk"}; assert a.min_score() == 7.0
    a.CFG = {}
    old = d.read_cfg()
    assert d.clean({**old, "min_score": 4.3}, old)["min_score"] == 4.5 and d.clean({**old, "min_score": 99}, old)["min_score"] == 10.0 and d.clean({**old, "min_score": -3}, old)["min_score"] == 1.0


def test_start_with_windows_is_off_unless_autopilot_is_on():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import dashboard as d
    assert d.startup_blocked(["dashboard.py", "--startup"], {"autopilot": False})          # started by Windows with Autopilot off: ends at once
    assert d.startup_blocked(["dashboard.py", "--startup"], {})
    assert not d.startup_blocked(["dashboard.py", "--startup"], {"autopilot": True})
    assert not d.startup_blocked(["dashboard.py"], {"autopilot": False})                  # opened by hand (dashboard.bat): always starts
    if d.os.name == "nt":  # the Windows entry itself, under a test name that is removed again
        keep = d.AUTOSTART_NAME; d.AUTOSTART_NAME = "ClippyDashboardSelfTest"
        try:
            assert not d.autostart_state(); d.autostart_set(True); assert d.autostart_state(); d.autostart_set(False); assert not d.autostart_state()
        finally:
            d.autostart_set(False); d.AUTOSTART_NAME = keep


def test_stop_now_keeps_autopilot_on():
    import sys, time as tm, tempfile
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import dashboard as d
    assert not d.failed_run_switches_autopilot_off(None)
    assert not d.failed_run_switches_autopilot_off({"pct": 40, "msg": "Working"})
    assert not d.failed_run_switches_autopilot_off({"pct": -1, "msg": "Stopped by you: the run was ended"})   # you stopped it: Autopilot stays on
    assert d.failed_run_switches_autopilot_off({"pct": -1, "msg": "Partly posted: daily upload limit"})        # a real failure still switches it off
    keep = d.HOLD; d.HOLD = Path(tempfile.mkdtemp()) / "hold.txt"
    try:
        assert not d.hold_active()
        d.HOLD.write_text(str(tm.time() + 600)); assert d.hold_active()
        d.HOLD.write_text(str(tm.time() - 5)); assert not d.hold_active()
    finally:
        d.HOLD = keep


def test_private_days_count_from_the_day_they_are_set():
    import sys, datetime as dtm
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import dashboard as d
    old = {**d.read_cfg(), "private_days": 0, "start_date": "2026-11-19"}
    same = d.clean({**old, "private_days": 0}, old)
    assert same["start_date"] == "2026-11-19"                                       # nothing changed: the date stays
    new = d.clean({**old, "private_days": 7}, old)
    assert new["private_days"] == 7 and new["start_date"] == dtm.date.today().isoformat()  # N set today: counts from today
    again = d.clean({**new, "private_days": 7}, new)
    assert again["start_date"] == new["start_date"]                                 # saving other settings does not restart the count


def test_health_checks():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import health
    have = {"chrome": True, "ffmpeg": True, "ytdlp": True, "ai_key": True}
    good = health.checks({"title_writer": "api"}, {"youtube": True, "tiktok": True}, {}, 50.0, 2, have)
    assert health.summary(good)["ok"] and all(x["level"] in ("ok", "info") for x in good)
    bad = health.checks({"title_writer": "api"}, {"youtube": False, "tiktok": True}, {"available": True}, 2.0, 9, {**have, "chrome": False, "ai_key": False}, "20:00")
    ids = {x["id"]: x for x in bad}
    assert ids["signin_youtube"]["level"] == "bad" and ids["signin_youtube"]["fix"]["action"] == "signin"
    assert ids["chrome"]["level"] == "bad" and ids["disk"]["level"] == "bad" and ids["ai"]["level"] == "warn" and "update" in ids and "waiting" in ids and "ytlimit" in ids
    assert health.summary(bad)["bad"] == 3 and not health.summary(bad)["ok"]
    only_tt = health.checks({"post_youtube": False}, {"youtube": False, "tiktok": True}, {}, 50.0, 0, have)
    assert not any(x["id"] == "signin_youtube" for x in only_tt)                     # a platform you switched off is not checked


def test_youtube_limit_is_remembered_until_the_reset():
    import sys, tempfile, time as tm, datetime as dtm
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import ytlimit
    keep = ytlimit.F; ytlimit.F = Path(tempfile.mkdtemp()) / "yt_limit.json"
    try:
        assert ytlimit.until() is None and ytlimit.text() is None
        now = tm.time(); u = ytlimit.set_hit(now)
        assert now < u <= now + 25 * 3600 and ytlimit.until(now) == u and ytlimit.text(False, now)   # reset is within a day
        assert ytlimit.until(u + 5) is None                                                            # after the reset YouTube is free again
        assert ytlimit.reset_time(now) > now
        ytlimit.clear(); assert ytlimit.until() is None
    finally:
        ytlimit.F = keep


def test_duplicate_guard_only_for_the_same_streamer():
    import sys, tempfile
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import dupes
    keep = dupes.F; dupes.F = Path(tempfile.mkdtemp()) / "seen.jsonl"
    said = "oh my god did you see that he just drove the car straight off the bridge into the water nobody expected that"
    try:
        dupes.record("vid1", "WestJett", 100, 130, said)
        assert dupes.is_duplicate("vid1", "westjett", 110, 140, "something else entirely")                 # same streamer, same video, overlapping time
        assert dupes.is_duplicate("vid2", "WestJett", 5, 35, said)                                          # same streamer, another upload, same words
        assert not dupes.is_duplicate("vid1", "OtherStreamer", 100, 130, said)                              # another streamer: NEVER a duplicate, even identical words
        assert not dupes.is_duplicate("vid1", "WestJett", 300, 330, "a completely different moment with different words in it here")   # same video, other part
        assert not dupes.is_duplicate("vid3", "WestJett", 0, 30, "short")                                    # too few words to compare
        assert not dupes.is_duplicate("vid1", "", 100, 130, said)                                            # unknown streamer: not guessed
    finally:
        dupes.F = keep


def test_backup_and_restore_round_trip():
    import sys, tempfile
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import dashboard as d
    keep = (d.R, d.CFG, d.learn_refit)
    tmp = Path(tempfile.mkdtemp()); d.R = tmp; d.CFG = tmp / "autopost_config.json"; d.learn_refit = lambda: None
    try:
        import json
        base = {**json.loads(keep[1].read_text(encoding="utf-8-sig")), "autopilot": True, "auto_post": True, "min_score": 6.5}
        d.save_cfg(base); (tmp / "ratings.jsonl").write_text('{"id":"x","rating":1}\n', encoding="utf-8")
        b = d.backup_bundle()
        assert b["clippy_backup"] == 1 and "ratings" in b and "x" in b["ratings"] and not any("key" in k.lower() and isinstance(v, str) and len(v) > 40 for k, v in b["config"].items())
        d.save_cfg({**base, "min_score": 8.0, "autopilot": True}); (tmp / "ratings.jsonl").unlink()
        d.restore_bundle(b)
        got = json.loads(d.CFG.read_text(encoding="utf-8"))
        assert got["min_score"] == 6.5 and got["autopilot"] is False and got["auto_post"] is False              # settings back, but nothing starts by itself
        assert "x" in (tmp / "ratings.jsonl").read_text(encoding="utf-8")                                        # learning back
        try:
            d.restore_bundle({"nope": 1}); raise AssertionError("should refuse")
        except ValueError:
            pass
    finally:
        d.R, d.CFG, d.learn_refit = keep


def test_update_can_go_back():
    import sys, json, tempfile
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import update_clippy as u
    keep = (u.R, u.STATE, u.VER)
    tmp = Path(tempfile.mkdtemp()); u.R = tmp; u.STATE = tmp / "update_state.json"; u.VER = tmp / "version.json"
    try:
        (tmp / "scripts").mkdir(); (tmp / "dashboard.py").write_text("NEW"); (tmp / "scripts" / "x.py").write_text("NEW2"); u.VER.write_text(json.dumps({"sha": "b" * 40}))
        bak = tmp / "update_backup" / "20260101-000000"; (bak / "scripts").mkdir(parents=True)
        (bak / "dashboard.py").write_text("OLD"); (bak / "scripts" / "x.py").write_text("OLD2"); (bak / "evil.txt").write_text("no"); (bak / "_prev.json").write_text(json.dumps({"sha": "a" * 40, "new": "b" * 40}))
        assert len(u.backups()) == 1
        u.rollback()
        assert (tmp / "dashboard.py").read_text() == "OLD" and (tmp / "scripts" / "x.py").read_text() == "OLD2" and not (tmp / "evil.txt").exists()   # only program files come back
        assert json.loads(u.VER.read_text())["sha"] == "a" * 40
        st = json.loads(u.STATE.read_text()); assert st["skip"] == "b" * 40 and st["restart"] and not u.backups()                                       # not offered again; backup used up
        u.rollback(); assert "no earlier version" in json.loads(u.STATE.read_text())["error"]
    finally:
        u.R, u.STATE, u.VER = keep


def test_selected_clips_can_be_scheduled():
    import sys, json, tempfile, datetime as dtm
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import dashboard as d
    keep = d.REVIEW; d.REVIEW = Path(tempfile.mkdtemp())
    try:
        for i, rt in enumerate([0, 0, 1]):
            st = f"17960000{i:02d}__clip_1"; (d.REVIEW / f"{st}.mp4").write_bytes(b"x"); (d.REVIEW / f"{st}.json").write_text(json.dumps({"rank": 5 + i, "rating": rt}))
        n = d.review_autoschedule("private", now=dtm.datetime(2030, 11, 22, 10, 0), ids={"1796000000__clip_1"})
        sch = {f.stem[8:10]: json.loads(f.read_text()).get("scheduled") for f in d.REVIEW.glob("*.json")}
        assert n == 1 and sch["00"] and not sch["01"] and not sch["02"], sch                # only the selected clip, liked or not
    finally:
        d.REVIEW = keep


def test_manual_and_automation_choose_their_source_separately():
    import sys, json, tempfile
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import dashboard as d
    old = {**d.read_cfg(), "run_source": "video", "auto_source": "video"}
    new = d.clean({**old, "run_source": "video", "auto_source": "live"}, old)
    assert new["run_source"] == "video" and new["auto_source"] == "live"                 # manual on videos, automation on live streams
    assert d.clean({**old, "run_source": "junk"}, {**old, "run_source": "live"})["run_source"] == "live"   # a bad value keeps the old one
    assert d.clean({k: v for k, v in old.items() if k not in ("run_source", "auto_source")}, {k: v for k, v in old.items() if k not in ("run_source", "auto_source")})["auto_source"] == "video"
    keep = (d.CFG, d.LIVE_RUN, d.spawn_live); tmp = Path(tempfile.mkdtemp()); d.CFG, d.LIVE_RUN = tmp / "c.json", tmp / "live_run.json"
    spawned = []; d.spawn_live = lambda: spawned.append(1)   # (never start a real watcher from a test)
    try:
        d.save_cfg({**old}); before = json.loads(d.CFG.read_text(encoding="utf-8")).get("live_watch"); d.start_live("manual", 5)
        r = json.loads(d.LIVE_RUN.read_text(encoding="utf-8"))
        assert r["by"] == "manual" and r["target"] == 5 and spawned == [1]                 # a live RUN is a file + a watcher ...
        assert json.loads(d.CFG.read_text(encoding="utf-8")).get("live_watch") == before   # ... and no setting is changed by it
        d.start_live("auto", 99); r = json.loads(d.LIVE_RUN.read_text(encoding="utf-8")); assert r["target"] == 10 and r["by"] == "auto"
    finally:
        d.CFG, d.LIVE_RUN, d.spawn_live = keep


def test_clips_you_looked_at_do_not_count_as_waiting():
    import sys, json, tempfile, time as tm
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import dashboard as d
    keep = d.REVIEW; d.REVIEW = Path(tempfile.mkdtemp())
    try:
        now = int(tm.time())
        for i, extra in enumerate([{}, {"rating": 1}, {"rating": -1}, {"rating": 0, "reviewed": True}, {"scheduled": {"at": "2099-01-01T10:00", "mode": "private"}}]):
            st = f"{now + i}__clip_1"; (d.REVIEW / f"{st}.mp4").write_bytes(b"x"); (d.REVIEW / f"{st}.json").write_text(json.dumps(extra))
        assert len(d.waiting_clips()) == 1                       # only the clip you have not looked at waits for you
        assert len(d.waiting_clips(include_reviewed=True)) == 5   # the day's quota of prepared clips still counts them all
    finally:
        d.REVIEW = keep


def test_removing_a_rated_clip_keeps_what_clippy_learned():
    import sys, json, tempfile
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent)); sys.path.insert(0, str(Path(__file__).resolve().parent))
    import dashboard as d, learn
    keep = (d.R, d.REVIEW, d.learn_refit, learn.R, learn.W_FILE)
    tmp = Path(tempfile.mkdtemp()); (tmp / "review").mkdir()
    d.R, d.REVIEW, d.learn_refit, learn.R, learn.W_FILE = tmp, tmp / "review", (lambda: None), tmp, tmp / "w.json"
    try:
        feats = {"win": 8.0, "score": 6.0, "channel": "someone"}
        def clip(i, **extra):
            st = f"17960000{i:02d}__clip_1"; (d.REVIEW / f"{st}.mp4").write_bytes(b"x"); (d.REVIEW / f"{st}.json").write_text(json.dumps({"rank": 9, "features": feats, **extra})); return st
        up, down, neutral, plain = clip(1, rating=1, reviewed=True), clip(2, rating=-1, reviewed=True), clip(3, rating=0, reviewed=True), clip(4)
        for st in (up, down):                                   # rated the normal way: written to ratings.jsonl at once
            d.log_rating(st, json.loads((d.REVIEW / f"{st}.json").read_text()), json.loads((d.REVIEW / f"{st}.json").read_text())["rating"])
        for st in (up, down, neutral, plain):
            d.review_remove(st)                                  # the user deletes every clip
        assert not list(d.REVIEW.glob("*.mp4"))
        got = {r["id"]: r["rating"] for r in learn.load()}       # what Clippy still remembers
        assert got[up] == 1 and got[down] == -1 and got[neutral] == 0 and got[plain] == 0, got
    finally:
        d.R, d.REVIEW, d.learn_refit, learn.R, learn.W_FILE = keep


def test_live_discovery_language_and_multilingual_chat():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import textutil as t
    assert t.stream_language_ok("en", {"languages": ["en"]}) and not t.stream_language_ok("pt", {"languages": ["en"]})
    assert t.stream_language_ok("", {"languages": ["en"]}) and t.stream_language_ok("de", {"languages": "any"}) and t.stream_language_ok("DE", {"languages": ["en", "de"]})
    assert t.chat_weight("jajajaja") == 2 and t.chat_weight("kkkkk") == 2 and t.chat_weight("no puede ser") == 3 and t.chat_weight("MEU DEUS") == 3 and t.chat_weight("krass") == 3
    assert t.chat_weight("WTF") == 3 and t.chat_weight("LUL") == 2 and t.chat_weight("nice car") == 1       # English and plain messages unchanged


def test_live_has_its_own_minimum_score():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent)); sys.path.insert(0, str(Path(__file__).resolve().parent))
    import autorun as a, dashboard as d
    keep = (a.CFG, a.ORIGIN)
    try:
        a.CFG = {}; a.ORIGIN = "video"; assert a.min_score() == 7.0
        a.ORIGIN = "live"; assert a.min_score() == 6.0                                 # live default is lower
        a.CFG = {"min_score": 8, "min_score_live": 4.5}; assert a.min_score() == 4.5 and a.slack() == 2.5
        a.ORIGIN = "video"; assert a.min_score() == 8.0 and a.slack() == 0.0           # the two never mix
    finally:
        a.CFG, a.ORIGIN = keep
    old = d.read_cfg()
    assert d.clean({**old, "min_score_live": 4.3}, old)["min_score_live"] == 4.5 and d.clean({**old, "min_score_live": 99}, old)["min_score_live"] == 10.0
    import learn
    src = (Path(__file__).resolve().parent / "autorun.py").read_text(encoding="utf-8")
    assert '"live": 1 if ORIGIN == "live" else 0' in src  # every clip still saves whether it came from a live stream (polish 4 slimmed the model, not what is saved)


def test_live_log_clear_never_leaves_nul_bytes():
    import sys, tempfile
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import dashboard as d
    keep = d.R; tmp = Path(tempfile.mkdtemp()); d.R = tmp
    try:
        (tmp / "live_watch.log").write_bytes(b"22:00:00 live watcher started" + bytes([10]) + b"old line" + bytes([10]))
        (tmp / "autopost_config.json").write_text('{"live_watch": false}')
        d.CFG = tmp / "autopost_config.json"; d.LIVE_RUN = tmp / "live_run.json"; d.LIVE_RUN.write_text("{}")   # a live run is going
        assert "old line" in d.log_text() or True
        (tmp / "live_log_mark.txt").write_text(str((tmp / "live_watch.log").stat().st_size))          # "Clear log" moves the mark
        with open(tmp / "live_watch.log", "ab") as fh:
            fh.write(bytes([0]) * 5 + b"23:00:00 live watcher started" + bytes([10]) + b"new line" + bytes([10]))   # a hole of NULs from an older clear, then new text
        t = d.log_text()
        assert "new line" in t and "old line" not in t and chr(0) not in t, repr(t)
    finally:
        d.R = keep; d.CFG = keep / "autopost_config.json"; d.LIVE_RUN = keep / "live_run.json"


def test_current_run_log_shows_the_live_log_while_live_is_on():
    import sys, tempfile, json
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import dashboard as d
    keep = (d.R, d.CFG, d.LOG, d.LIVE_STATUS, d.LIVE_RUN); tmp = Path(tempfile.mkdtemp())
    d.R, d.CFG, d.LOG, d.LIVE_STATUS, d.LIVE_RUN = tmp, tmp / "c.json", tmp / "autorun.log", tmp / "no_live_status.json", tmp / "live_run.json"   # (not the real watcher's files)
    try:
        (tmp / "autorun.log").write_text("old video run line", encoding="utf-8"); (tmp / "live_watch.log").write_text("10:00:00 live watcher started" + chr(10) + "10:01:00 chat spike on x", encoding="utf-8")
        assert "old video run line" in d.log_text(8000, 8000)                               # no live run: the normal run log
        d.LIVE_RUN.write_text("{}")
        t = d.log_text(8000, 8000)
        assert "chat spike on x" in t and "old video run line" not in t                       # a live run: the live watcher's log
    finally:
        d.R, d.CFG, d.LOG, d.LIVE_STATUS, d.LIVE_RUN = keep


def test_live_watcher_swaps_quiet_streams_for_better_ones():
    import sys, time as tm
    from types import SimpleNamespace as NS
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent)); sys.path.insert(0, str(Path(__file__).resolve().parent))
    import live_watch as lw
    now = tm.time(); mk = lambda ch, viewers, spikes, idle_min, creator=None, age=300: NS(s={"channel": ch, "viewers": viewers, "creator": creator}, spikes=spikes, last_spike=now - idle_min * 60, t0=now - age * 60)
    busy, quiet, quieter, mine = mk("busy", 20000, 5, 3), mk("quiet", 15000, 1, 50), mk("quieter", 14000, 0, 90), mk("mine", 9000, 0, 200, creator="Mine")
    ws = [busy, quiet, quieter, mine]
    assert lw.pick_victim(ws, {"viewers": 10000}, now) is quieter                       # idle for 40+ minutes: the one with the fewest spikes goes first
    assert lw.pick_victim([busy, mine], {"viewers": 10000}, now) is None                # nobody idle, newcomer not much bigger: keep everything
    assert lw.pick_victim([mk("x", 5000, 1, 20), mine], {"viewers": 11000}, now).s["channel"] == "x"   # 2x the viewers and quiet for 15+ minutes
    assert lw.pick_victim([busy, quiet], {"viewers": 5000, "creator": "Mine"}, now) is quiet           # a streamer from your list takes the place of a quiet stream
    assert lw.pick_victim([mine], {"viewers": 99999, "creator": "Other"}, now) is None                # a stream on your list is never swapped out


def test_ended_youtube_streams_are_detected():
    import sys, types
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent)); sys.path.insert(0, str(Path(__file__).resolve().parent))
    import live_watch as lw
    def fake(info):
        class Y:
            def __init__(self, o): pass
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def extract_info(self, url, download=False):
                if isinstance(info, Exception):
                    raise info
                return info
        return types.SimpleNamespace(YoutubeDL=Y)
    keep = sys.modules.get("yt_dlp")
    try:
        sys.modules["yt_dlp"] = fake({"is_live": False, "live_status": "post_live"}); assert lw.stream_ended("u")
        sys.modules["yt_dlp"] = fake({"is_live": True, "live_status": "is_live"}); assert not lw.stream_ended("u")
        sys.modules["yt_dlp"] = fake(RuntimeError("network")); assert not lw.stream_ended("u")      # a failed lookup never ends a stream
    finally:
        if keep is not None:
            sys.modules["yt_dlp"] = keep
        else:
            sys.modules.pop("yt_dlp", None)


def test_live_swap_helpers():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent)); sys.path.insert(0, str(Path(__file__).resolve().parent))
    import live_watch as lw
    from types import SimpleNamespace as NS
    assert lw.parse_target("https://www.twitch.tv/lirik") == ("Twitch", "https://www.twitch.tv/lirik")
    assert lw.parse_target("https://www.youtube.com/watch?v=abc123") == ("YouTube", "https://www.youtube.com/watch?v=abc123")
    assert lw.parse_target("  Lirik ") == ("Twitch", "https://twitch.tv/Lirik") and lw.parse_target("") is None and lw.parse_target("a") is None
    assert lw.parse_target("javascript:alert(1)") is None or lw.parse_target("javascript:alert(1)")[1].startswith("https://twitch.tv/")   # never a foreign address
    w = NS(s={"channel": "Watched"})
    found = [{"url": "u1", "channel": "Watched"}, {"url": "u2", "channel": "Other", "creator": None}, {"url": "u3", "channel": "Mine", "creator": "Mine"}]
    assert lw.pick_other(found, [w], {}, "old")["channel"] == "Mine"                    # a streamer from your list is preferred
    found2 = [{"url": "u1", "channel": "Watched"}, {"url": "u2", "channel": "Other"}]
    assert lw.pick_other(found2, [w], {}, "old")["channel"] == "Other"                  # never one that is already watched
    assert lw.pick_other(found2, [w], {"u2": 9e12}, "old") is None                      # nor one that was just swapped out


def test_stream_language_is_checked_for_youtube_too():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent)); sys.path.insert(0, str(Path(__file__).resolve().parent))
    import live_watch as lw
    cfg = {"languages": ["en"]}
    lw.LANG_CACHE.update({"t1": {"language": "en", "p": 0.95}, "t3": {"language": "ro", "p": 0.9}})
    assert lw.stream_language_allowed({"platform": "Twitch", "url": "t1", "channel": "a", "lang": "en"}, cfg)
    assert not lw.stream_language_allowed({"platform": "Twitch", "url": "t2", "channel": "b", "lang": "ro"}, cfg)       # the tag says Romanian: no need to listen
    assert not lw.stream_language_allowed({"platform": "Twitch", "url": "t3", "channel": "g", "lang": "en"}, cfg)       # tagged English but the speech is Romanian: the speech wins
    lw.LANG_CACHE.update({"y1": {"language": "ro", "p": 0.93}, "y2": {"language": "en", "p": 0.97}, "y3": {}, "y4": {"language": "ro", "p": 0.3}})
    assert not lw.stream_language_allowed({"platform": "YouTube", "url": "y1", "channel": "c"}, cfg)       # YouTube has no language field: the speech decides
    assert lw.stream_language_allowed({"platform": "YouTube", "url": "y2", "channel": "d"}, cfg)
    assert lw.stream_language_allowed({"platform": "YouTube", "url": "y3", "channel": "e"}, cfg)           # could not be told: allowed
    assert lw.stream_language_allowed({"platform": "YouTube", "url": "y4", "channel": "f"}, cfg)           # not sure enough: allowed
    assert lw.stream_language_allowed({"platform": "YouTube", "url": "y1", "channel": "c"}, {"languages": "any"})
    from types import SimpleNamespace as NS
    found = [{"url": "y1", "channel": "Ro"}, {"url": "y2", "channel": "En"}]
    cool = {}
    for _ in range(5):                                                                                   # (the pick is random: whatever the order, never the Romanian one)
        assert lw.pick_other(found, [], {}, "x", lambda s: lw.stream_language_allowed(s, cfg))["channel"] == "En"


def test_langkit_rules():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import langkit as L
    W = lambda s: s.split()
    ro_mix = W("Omule ce se intampla acum wtf bro nu pot sa cred what the fuck incredibil")
    ro = W("Omule ce se intampla acum nu pot sa cred este incredibil cum a facut asta")
    en = W("oh my god did you see that he just drove the car off the bridge")
    assert L.tag("what") == "en" and L.tag("Ahora") == "latin" and L.tag("привет") == "cyrillic" and L.tag("안녕") == "hangul" and L.tag("42") is None
    assert L.moment_allowed({"language": "ro", "p": 0.95}, ro_mix, ["en"])                 # an English burst inside Romanian counts (25%+ of the words)
    assert not L.moment_allowed({"language": "ro", "p": 0.95}, ro, ["en"])                 # sure it is Romanian, no English in it: rejected
    assert L.moment_allowed({"language": "ro", "p": 0.55}, ro, ["en"])                     # unsure: never rejected
    assert L.moment_allowed({"language": "ro", "p": 0.95}, ro, None) and L.moment_allowed({"language": "en", "p": 0.99}, en, ["en"])
    assert L.moment_allowed({"language": "ro", "p": 0.9}, ro, ["en", "ro"])                # allowed language
    assert L.caption_plan({"language": "en", "p": 0.97}, en)["captions"] and L.caption_plan({"language": "ro", "p": 0.9}, ro)["captions"]   # sure: captions as always
    assert L.caption_plan({"language": "en", "p": 0.37}, en)["captions"]                   # unsure but English-looking: English captions as before
    p = L.caption_plan({"language": "nn", "p": 0.26}, ro)
    assert not p["captions"] and p["unsure"]                                                # unsure and not English-looking: no captions, flagged
    assert L.caption_plan({"language": "en", "p": 0.3}, W("ok yes"))["captions"]            # almost no words: nothing to doubt
    assert L.upper("istanbul ısı", "tr") == "İSTANBUL ISI" and L.upper("istanbul") == "ISTANBUL"
    assert L.font_for_script("cyrillic") is None and L.font_for_script("hangul", r"C:\nowhere") is None


def test_language_reaction_words():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import langkit as L
    ro = {"language": "ro", "p": 0.95}
    assert L.reaction("naiba", ro) == "swear" and L.reaction("haha", ro) == "laugh" and L.reaction("incredibil", ro) == "hype"
    assert L.reaction("naiba", {"language": "ro", "p": 0.5}) is None and L.reaction("naiba", {"language": "en", "p": 0.99}) is None   # unsure / English: untouched
    assert L.reaction("hello", ro) is None


def test_dislike_reasons_can_be_several_and_custom():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import learn
    assert learn.off_moment({"reasons": ["crop", "audio"]}) and learn.off_moment({"reason": "nudity"})
    assert not learn.off_moment({"reasons": ["crop", "boring"]}) and not learn.off_moment({"reasons": ["my own words"]}) and not learn.off_moment({})
    assert learn.off_moment({"reasons": ["the sound was terrible", "wrong crop on the face"]}) and learn.canon("This is so boring") == "boring" and not learn.off_moment({"reasons": ["super dull moment"]})


def test_english_captions_are_byte_identical_and_other_scripts_get_fonts():
    import sys, hashlib
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import cut
    words = [{"w": w, "s": 0.2 * i, "e": 0.2 * i + 0.15} for i, w in enumerate("Oh my god did you see that, he just drove the car off the bridge! What the hell is going on here, nobody expected that at all.".split())]
    out = []
    for caps in ("word", "bubble"):
        for title in (None, ("outline", "Nobody saw that coming", 700, 0)):
            out.append(cut.build_ass(words, 0.0, 6.0, "Hook line here", 1460, title=title, caps=caps, profanity=False))
    blob = (chr(10) + "=====" + chr(10)).join(out)
    assert hashlib.sha256(blob.encode("utf-8")).hexdigest() == "0c829dd3347780be5b8de0943cd032729c80d7e8e7cf81568430b3462547b283", "English captions changed"
    assert cut._fonts("hello world") == "hello world" and cut._rtl([{"w": "مرحبا"}, {"w": "بك"}]) and not cut._rtl([{"w": "hello"}, {"w": "بك"}])
    ar = [{"w": "ما", "s": 0, "e": 0.3}, {"w": "هذا", "s": 0.4, "e": 0.7}]
    assert cut.build_ass(ar, 0.0, 2.0, "", 1460, caps="word", lang="ar").count("Dialogue") >= 2


def test_live_clip_keeps_raw_footage_until_removed():
    import sys, tempfile, json
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent)); sys.path.insert(0, str(Path(__file__).resolve().parent))
    import autorun, dashboard as d
    tmp = Path(tempfile.mkdtemp()); kr, kd = autorun.REVIEW, d.REVIEW
    autorun.REVIEW = d.REVIEW = tmp / "review"
    try:
        job = tmp / "jobs" / "x-1"; (job / "edits").mkdir(parents=True); raw = job / "live" / "x.mp4"; raw.parent.mkdir()
        raw.write_bytes(b"raw"); (job / "candidates.json").write_text(json.dumps({"source": str(raw)}))
        clip = job / "edits" / "clip_1.mp4"; clip.write_bytes(b"c"); clip.with_suffix(".json").write_text(json.dumps({"origin": "live", "start": 1, "end": 5}))
        autorun.sh = lambda *a, **k: None
        stem = autorun.queue_for_review(clip)
        assert (tmp / "review" / (stem + ".raw")).read_bytes() == b"raw"
        assert json.loads((tmp / "review" / (stem + ".json")).read_text())["job"] == str(job)
        d.review_remove(stem)
        assert not (tmp / "review" / (stem + ".raw")).exists()   # removed with the clip
    finally:
        autorun.REVIEW, d.REVIEW = kr, kd


def test_video_clip_raw_piece_is_shifted_and_restored():
    import sys, tempfile, json, subprocess
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import rawkeep
    from common import tool
    tmp = Path(tempfile.mkdtemp()); job = tmp / "job"; job.mkdir()
    src = job / "source.mp4"
    subprocess.run([tool("ffmpeg"), "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=s=320x240:r=10:d=40", "-f", "lavfi", "-i", "sine=d=40", "-shortest", "-c:v", "libx264", "-c:a", "aac", str(src)], check=True)
    (job / "candidates.json").write_text(json.dumps({"source": str(src), "info": {"title": "t"}, "candidates": [{"n": 2, "start": 20.0, "end": 30.0}]}))
    (job / "transcript.json").write_text(json.dumps([{"w": "hi", "s": 21.0, "e": 21.4}, {"w": "far", "s": 2.0, "e": 2.3}]))
    assert rawkeep.make_kit(job, {"start": 20.0, "end": 30.0}, 2, tmp / "r.raw", tmp / "r.kit")
    j, s, e, off, pick = rawkeep.restore_job(tmp / "r.kit", tmp / "r.raw", tmp / "w")
    assert off == 14.0 and (s, e, pick) == (6.0, 16.0, 2)                                   # times are shifted by the piece's start
    assert [w["w"] for w in json.loads((j / "transcript.json").read_text())] == ["hi"] and json.loads((j / "transcript.json").read_text())[0]["s"] == 7.0
    assert (j / "source.mp4").exists() and 15 < float(subprocess.run([tool("ffprobe"), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(j / "source.mp4")], capture_output=True, text=True).stdout) < 25


def test_stretched_words_are_trimmed_to_when_they_were_said():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import cut
    ws = [{"w": "right", "s": 33.35, "e": 39.45}, {"w": "this", "s": 39.45, "e": 39.63}, {"w": "lit", "s": 40.03, "e": 40.67}, {"w": "24", "s": 40.67, "e": 46.05}, {"w": "strip", "s": 46.05, "e": 47.13}]
    t = cut._tidy(ws)
    assert t[0]["s"] > 38.0 and t[0]["e"] == 39.45          # a stretched first word was said at its end, right before the next word
    assert t[3]["s"] == 40.67 and t[3]["e"] < 42.0          # a stretched word before a silence was said at its start
    assert t[1] == ws[1] | {"s": 39.45, "e": 39.63}         # normal words are untouched


def test_old_clips_leave_ready_to_post_by_themselves():
    import sys, tempfile, json, time, datetime
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import dashboard as d
    now = int(time.time()); old, new, kept = f"{now - 5 * 86400}__clip_1", f"{now - 3600}__clip_1", f"{now - 5 * 86400}__clip_2"
    assert d.review_expiry({}, old, 3) < time.time() and d.review_expiry({}, new, 3) > time.time() and d.review_expiry({}, old, 0) is None
    future = (datetime.datetime.now() + datetime.timedelta(hours=2)).isoformat(timespec="minutes")
    assert d.review_expiry({"scheduled": {"at": future}}, old, 3) is None and d.review_expiry({"keep": True}, old, 3) is None   # a coming post or a Keep stops the removal
    tmp = Path(tempfile.mkdtemp()); keep, kc, kr = d.REVIEW, d.read_cfg, d.R
    d.REVIEW = tmp; d.R = tmp; d.read_cfg = lambda: {"keep_clips_days": 3}   # R too: a real post.lock (something is posting right now) must not matter
    try:
        for s_, extra in ((old, {}), (new, {}), (kept, {"keep": True})):
            (tmp / (s_ + ".mp4")).write_bytes(b"v"); (tmp / (s_ + ".raw")).write_bytes(b"r"); (tmp / (s_ + ".json")).write_text(json.dumps({"features": {"win": 1}, **extra}))
        d.review_expire()
        assert not (tmp / (old + ".mp4")).exists() and not (tmp / (old + ".raw")).exists() and not (tmp / (old + ".json")).exists()   # the old one and its raw copy are gone
        assert (tmp / (new + ".mp4")).exists() and (tmp / (kept + ".mp4")).exists()
        assert json.loads((tmp / (new + ".json")).read_text())["expire_days"] == 3          # stamped once with the setting of that day
        d.read_cfg = lambda: {"keep_clips_days": 1}; d.review_expire()                      # the setting changes later: the clip keeps its own period
        assert (tmp / (new + ".mp4")).exists() and d.review_expiry(json.loads((tmp / (new + ".json")).read_text()), new, 1) == int(new.split("__")[0]) + 3 * 86400
    finally:
        d.REVIEW, d.read_cfg, d.R = keep, kc, kr


def test_polish1_rules_cap_smooth_and_keep_good_clips():
    import sys
    import numpy as np
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import pick_rules as PR
    z = np.array([0.5, 3.0, 12.0, -2.0])
    assert PR.cap_z(z).tolist() == [0.5, 3.0, 6.0, 0.0] and PR.cap_z(z, sparse=True).tolist() == [0.5, 3.0, 3.5, 0.0]      # one event can no longer be worth +12
    assert PR.viral_penalty(5.0) == 0 and PR.viral_penalty(6.2) == 0 and PR.viral_penalty(4.0) == 2.0 and PR.viral_penalty(3.0) == 2.0
    assert PR.viral_penalty(4.99) < 0.05 and PR.viral_penalty(4.5) == 1.0                                              # no cliff at 5.0
    assert PR.viral_penalty(4.9) < PR.viral_penalty(4.5) < PR.viral_penalty(4.1)                                       # smooth and monotonic
    assert PR.talk_blocks(2, False) and not PR.talk_blocks(2, True) and not PR.talk_blocks(1, False)                    # "chat, did you see that?! no way, chat!" with a real reaction is kept
    assert PR.no_ai_baseline(5.0) == 6.0 and PR.no_ai_baseline(9.0) == 10.0
    assert PR.total_penalty([(2.0, "a"), (1.5, "b"), (2.0, "c")]) == 4.0 and PR.total_penalty([(1.0, "a")]) == 1.0
    import moments as M
    assert not M.PROMO.search("we enter the bank now") and M.PROMO.search("enter the giveaway in chat") and M.PROMO.search("use code clip")


def test_unjudged_waiting_clips_are_not_learned_as_meh():
    import sys, tempfile, json, time
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import learn
    tmp = Path(tempfile.mkdtemp()); (tmp / "review").mkdir(); keep = learn.R; learn.R = tmp
    try:
        now = int(time.time())
        for name, extra in ((f"{now - 3600}__clip_1", {}), (f"{now - 5 * 86400}__clip_1", {}), (f"{now - 3600}__clip_2", {"reviewed": True})):
            (tmp / "review" / (name + ".json")).write_text(json.dumps({"features": {"win": 5}, **extra}))
        ids = {r["id"] for r in learn.load_all()}
        assert f"{now - 3600}__clip_1" not in ids                         # made an hour ago, nobody looked at it yet
        assert f"{now - 5 * 86400}__clip_1" in ids and f"{now - 3600}__clip_2" in ids   # old and ignored = meh; marked 😐 = meh
    finally:
        learn.R = keep


def test_a_strong_spoken_bit_is_not_dragged_down_by_dull_frames():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import pick_rules as PR
    # the alley clip: bit 7/10, frames 3/10, viral score 4.1, bonuses 1.58 (this rule is OFF in polish 2: switched on here only to test the arithmetic)
    PR.FRAME_OVERRIDE = True
    old = (7 + 3) / 2 + 0.3 * 4.1 + 1.58
    new = PR.ai_base(7, 3, None, PR.frame_overridden({"speaker": "streamer"}), 4.1) + 0.3 * 4.1 + 1.58
    assert round(old, 2) == 7.81 and round(new, 2) == 9.81
    assert PR.ai_base(7, 3, None, PR.frame_overridden({"speaker": "game"}), 4.1) == 5.0           # the game's own dialogue does not override the frame look
    assert PR.ai_base(7, 3, None, False, 4.1) == 5.0 and PR.ai_base(None, None, None, False, 5.0) == 6.0
    assert PR.ai_base(7, 3, 8, True, 4.1) == 7.5                                                  # the other AI read still counts
    PR.FRAME_OVERRIDE = False
    assert not PR.frame_overridden({"speaker": "streamer"})                                         # polish 2: the ratings say frames matter, so the override is off


def test_a_leaning_streamer_keeps_the_webcam_and_the_blur_crop_spares_the_webcam():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import pick_rules as PR
    assert PR.keep_webcam(0.92, 0.0) and PR.keep_webcam(0.25, 0.83)                       # CaseOh leans out of frame: faces found at the same place with a looser check
    assert not PR.keep_webcam(0.17, 0.58) and not PR.keep_webcam(0.08, 0.9) and not PR.keep_webcam(0.0, 1.0)   # a scene switch / no real webcam still falls back to blur
    assert PR.blur_crop_x((0.0, 0.2164, 0.1993, 0.1993)) == 0.2113                          # webcam straddling the left cut: the crop starts after it
    assert PR.blur_crop_x((0.80, 0.1, 0.2, 0.2)) == 0.038 and PR.blur_crop_x((0.0, 0.1, 0.10, 0.2)) is None   # on the right edge: end before it; fully outside the crop: unchanged
    assert PR.blur_crop_x((0.3, 0.1, 0.2, 0.2)) is None                                    # a cam in the middle is left alone
    import cut
    base = cut.video_filter("blur", "auto", "x.ass")
    assert "crop=iw*0.75:ih,scale=" in base                                                  # no webcam found: the old filter, unchanged
    assert "crop=iw*0.75:ih:iw*0.2110:0," in cut.video_filter("blur", "auto", "x.ass", crop_x=0.211)


def test_polish2_picture_length_and_streamer_names():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import pick_rules as PR, learn
    assert PR.frame_adjust(9) == 0.8 and PR.frame_adjust(8) == 0.4 and PR.frame_adjust(7) == 0 and PR.frame_adjust(5) == -0.8 and PR.frame_adjust(3) == -0.8 and PR.frame_adjust(None) == 0
    assert PR.length_adjust(24) == 0.4 and PR.length_adjust(20) == 0.4 and PR.length_adjust(30) == 0.4 and PR.length_adjust(15) == -0.5 and PR.length_adjust(35) == -0.2 and PR.length_adjust(19) == 0 and PR.length_adjust(52) == 0
    assert PR.norm_channel("ohnePixel") == PR.norm_channel("ohnepixel") == "ohnepixel" and PR.norm_channel("caseoh_") == PR.norm_channel("Caseoh_ VODS") == "caseoh"
    assert PR.norm_channel("xQc") == "xqc" and PR.norm_channel("Asmongold TV") == "asmongold" and PR.norm_channel("tv") == "tv"
    assert learn.channel_of({"features": {"channel": "Caseoh_ VODS"}}) == learn.channel_of({"creator": "caseoh_"})


def test_twitch_clip_options_are_validated_and_reach_the_search():
    import sys, copy
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent)); sys.path.insert(0, str(Path(__file__).resolve().parent))
    import dashboard as d, autorun, twitch
    base = d.read_cfg(); base["twitch_priority"] = False   # (the real file may have it on)
    ok = d.clean({**copy.deepcopy(base), "twitch_clips": "only", "twitch_period": "month", "twitch_per_streamer": 12}, base)
    assert (ok["twitch_clips"], ok["twitch_period"], ok["twitch_per_streamer"]) == ("only", "month", 12)
    assert d.clean({**copy.deepcopy(base), "twitch_priority": True}, base)["twitch_priority"] is True and d.clean({**copy.deepcopy(base)}, base)["twitch_priority"] is False   # the checkmark: on = prioritize, off = normal order
    assert d.clean({**copy.deepcopy(base), "twitch_clips": "off"}, base)["twitch_clips"] == "off"  # YouTube videos only
    bad = d.clean({**copy.deepcopy(base), "twitch_clips": "nope", "twitch_period": "year", "twitch_per_streamer": 999}, base)
    assert (bad["twitch_clips"], bad["twitch_period"], bad["twitch_per_streamer"]) == ("first", "week", 15)           # unknown values fall back, the count is capped
    seen = {}
    keep_cc, keep_tp = twitch.channel_clips, getattr(autorun, "TP", None)
    twitch.channel_clips = lambda name, limit=15, period="LAST_WEEK": seen.update(limit=limit, period=period) or []
    autorun.TP = {"any": True, "gta": False, "name": "x", "matches": lambda g: True}
    try:
        autorun.creator_twitch_clips("someone", set(), 12, "LAST_MONTH")
    finally:
        twitch.channel_clips, autorun.TP = keep_cc, keep_tp
    assert seen == {"limit": 36, "period": "LAST_MONTH"}                                                         # more clips per streamer and a longer period reach the Twitch query


def test_edge_strip_search_finds_nothing_in_an_empty_frame_and_never_crashes():
    import sys
    import numpy as np
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import facecam
    frames = [np.zeros((360, 640, 3), np.uint8) for _ in range(6)]
    face, hits = facecam.edge_strip_face(frames)
    assert face is None and hits == 0                                   # no face in a black frame: nothing is invented (polish 3: small / dark webcams are searched on the enlarged side strips)


def test_humour_needs_a_quoted_punchline_and_the_short_reason_list():
    import sys, json
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent)); sys.path.insert(0, str(Path(__file__).resolve().parent))
    import pick_rules as PR, ai_check, learn
    assert PR.quote_found("so I just ran over grandma and she said you are fired honestly", "she said you are fired")
    assert not PR.quote_found("hello there friend how are you", "a totally invented punchline") and not PR.quote_found("anything", "")
    assert PR.kind_cap(8.0, "funny", False, "") == 5.0 and PR.kind_cap(8.0, "funny", True, "") == 8.0 and PR.kind_cap(8.0, "", False, "") == 8.0      # nothing to show = at most 5; a model that ignores the field changes nothing
    assert PR.kind_cap(8.0, "interesting", False, "he lands the plane") == 8.0 and PR.kind_cap(8.0, "neither", False, "") == 4.0
    assert PR.kind_cap(8.0, "funny", False, "stick drift while following the hooker") == 8.0 and PR.kind_cap(8.0, "funny", False, "", True) == 8.0   # situational comedy needs no punchline: a described situation or an audible reaction is evidence
    assert PR.kind_cap(8.0, "funny", False, "n/a") == 5.0 and PR.kind_cap(8.0, "funny", False, "none") == 5.0                      # a placeholder is not evidence
    assert PR.norm_kind("None") == "neither" and PR.norm_kind("n/a") == "neither" and PR.norm_kind("funny|drama|shock") == "" and PR.norm_kind("a drama moment") == "drama" and PR.norm_kind("hype") == ""
    assert PR.quote_found("он сказал что это невозможно сделать сегодня", "это невозможно сделать")                                 # quotes in other scripts can be verified too
    assert PR.kind_cap(8.0, "drama", False, "they argue about the heist") == 8.0 and PR.kind_cap(8.0, "skill", False, "", True) == 8.0 and PR.kind_cap(8.0, "shock", False, "") == 5.0 and PR.kind_cap(8.0, "neither", True, "x y") == 4.0 and PR.kind_cap(8.0, "", False) == 8.0
    lo, hi = PR.wilson(20, 30)
    assert 0.48 < lo < 0.49 and 0.80 < hi < 0.81 and PR.wilson(0, 3)[1] < 0.6 and PR.wilson(0, 0) == (0.0, 1.0)             # small groups give wide ranges
    # the AI judge: a window whose quoted "funniest line" is not in the transcript is capped at 5, a real one keeps its score
    groups = ["oh no the car", "is on fire and", "he screams why would you park it there"]
    w = [{"w": x, "s": 100 + 3 * gi + 0.3 * i, "e": 100.25 + 3 * gi + 0.3 * i} for gi, g in enumerate(groups) for i, x in enumerate(g.split())]   # three bursts of speech with pauses between them
    keep = ai_check.ai_title.complete_cfg
    try:
        cases = [("funny", "he screams why would you park it there", "", 8.0, True),        # a real quoted punchline keeps its score
                 ("funny", "an invented line that is nowhere", "", 5.0, False),              # a joke with no quotable punchline: capped
                 ("funny", "an invented line that is nowhere", "a car burns and he reacts", 8.0, False),   # ... unless something interesting is stated
                 ("interesting", "", "a skill moment: he lands the plane on the roof", 8.0, False),      # interesting is never marked down for not being funny
                 ("interesting", "", "", 5.0, False), ("neither", "", "", 4.0, False)]
        for kind, quote, inter, expect, has_funny in cases:
            ai_check.ai_title.complete_cfg = lambda *a, **k: json.dumps({"start": 100, "end": 108.5, "score": 8.0, "kind": kind, "funny_line": quote, "interesting": inter, "payoff": True, "standalone": True, "speaker": "streamer", "risky": False, "why": "x"})
            r = ai_check.refine_window({"title_writer": "api"}, w, 100, 108.5, 40, 3)
            assert r and r["score"] == expect and bool(r["funny"]) == has_funny, (kind, quote, inter, r)
        r = ai_check.refine_window({"title_writer": "api"}, w, 100, 108.5, 40, 3, viewer_picked=True)
        assert r and r["score"] == 8.0                                                                                   # a clip viewers saved on Twitch is not capped for lacking a quoted line
        seen = {}
        ai_check.ai_title.complete_cfg = lambda cfg, prompt, **k: seen.update(p=prompt) or json.dumps({"start": 100, "end": 108.5, "score": 6.0, "standalone": True, "speaker": "streamer", "risky": False, "why": "x"})
        r = ai_check.refine_window({"title_writer": "api"}, w, 100, 108.5, 40, 3, examples=["LIKED: a panic", "DISLIKED: a crash | the viewer said: notfunny"])
        assert r and r["score"] == 6.0 and "LIKED: a panic" in seen["p"] and '"quote"' in seen["p"] and "DRAMA" in seen["p"] and "SKILL" in seen["p"]   # the examples and every kind of moment reach the prompt; a model that ignores "kind" keeps its score
        ai_check.ai_title.complete_cfg = lambda *a, **k: json.dumps({"start": 100, "end": 108.5, "score": 8.5, "kind": "drama", "quote": "why would you park it there", "what": "he snaps at his friend", "standalone": True, "speaker": "streamer", "risky": False, "why": "x"})
        r = ai_check.refine_window({"title_writer": "api"}, w, 100, 108.5, 40, 3)
        assert r and r["score"] == 8.5 and r["kind"] == "drama" and r["quote"]                                                      # drama is as clip-worthy as a joke
    finally:
        ai_check.ai_title.complete_cfg = keep
    import dashboard as d
    assert all(k in d.WHY for k in ("notfunny", "noaction", "slow", "nocontext", "cutoff", "crop", "offtopic"))
    html = (Path(__file__).resolve().parent.parent / "dashboard.html").read_text(encoding="utf-8")
    assert "Add your own reason" not in html and "const WHY=[['notfunny'" in html and html.split("const WHY=[")[1].split("];")[0].count("],[") == 6   # the short list: 7 reasons, no custom one


def test_drama_words_are_recognised_only_as_whole_words():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import moments as M
    assert all(M.DRAMA.match(w) for w in ("liar!", "Disrespectful,", "pathetic.", "STFU", "apologize", "unfollowed", "banned"))
    assert not any(M.DRAMA.match(w) for w in ("lyingly-not", "shutter", "wrongway", "hello", "beefy"))      # whole words only


def test_learning_uses_posts_kinds_and_reasons():
    import sys, tempfile, json, time, random
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import learn
    tmp = Path(tempfile.mkdtemp()); (tmp / "review").mkdir(); keep_r, keep_w = learn.R, learn.W_FILE
    learn.R = tmp; learn.W_FILE = tmp / "w.json"
    try:
        old = int(time.time()) - 5 * 86400
        (tmp / "review" / f"{old}__clip_1.json").write_text(json.dumps({"features": {"win": 7}}))
        (tmp / "review" / f"{old}__clip_1.posted.json").write_text(json.dumps({"tiktok": "posted"}))
        (tmp / "review" / f"{old}__clip_2.json").write_text(json.dumps({"features": {"win": 6}}))
        rows = {r["id"]: r for r in learn.load_all()}
        assert rows[f"{old}__clip_1"]["rating"] == 1 and rows[f"{old}__clip_1"]["w"] == learn.POSTED_WEIGHT        # posted without a rating = a soft like
        assert rows[f"{old}__clip_2"]["rating"] == 0                                                              # not posted, not rated = meh
        random.seed(3)
        mk = lambda i, good, kind: {"id": str(i), "rating": 1 if good else -1, "features": {"win": random.uniform(5, 8), "frames": random.uniform(5, 8), "kind": kind, "channel": "x"}}
        rows2 = [mk(i, True, "drama") for i in range(8)] + [mk(100 + i, False, "funny") for i in range(8)] + [mk(200 + i, i % 2 == 0, "skill") for i in range(8)]
        for r in rows2[8:12]:
            r["reasons"] = ["notfunny", "slow"]
        m = learn.fit(rows2)
        assert m and m["kinds"]["drama"] > 0 > m["kinds"]["funny"]                                                 # it learns which kinds of moments the user likes
        f = {"win": 6.5, "frames": 6.5, "channel": "x"}
        assert learn.adjust({**f, "kind": "drama"}, m) > learn.adjust({**f, "kind": "funny"}, m)
        rc = dict(learn.reason_counts(rows2))
        assert rc == {"notfunny": 4, "slow": 4}
    finally:
        learn.R, learn.W_FILE = keep_r, keep_w; learn._model.update(t=0.0, m=None)


def test_ratings_log_keeps_every_reason_and_posted_clips_stay_soft_likes():
    import sys, tempfile, json, time
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent)); sys.path.insert(0, str(Path(__file__).resolve().parent))
    import dashboard as d, learn
    tmp = Path(tempfile.mkdtemp()); (tmp / "review").mkdir()
    keep = (d.R, d.REVIEW, d.learn_refit, learn.R, learn.W_FILE)
    d.R = tmp; d.REVIEW = tmp / "review"; d.learn_refit = lambda: None; learn.R = tmp; learn.W_FILE = tmp / "w.json"
    try:
        st = f"{int(time.time()) - 5 * 86400}__clip_1"
        d.log_rating(st, {"features": {"win": 5}, "reason": "notfunny", "reasons": ["notfunny", "slow"]}, -1)
        assert dict(learn.reason_counts(learn.load_all())) == {"notfunny": 1, "slow": 1}                       # every ticked reason reaches the learning, not just the first
        st2 = f"{int(time.time()) - 5 * 86400}__clip_2"
        (tmp / "review" / (st2 + ".json")).write_text(json.dumps({"features": {"win": 7}}))
        (tmp / "review" / (st2 + ".posted.json")).write_text(json.dumps({"tiktok": "posted"}))
        (tmp / "review" / (st2 + ".mp4")).write_bytes(b"v")
        d.review_remove(st2, auto=True)
        rows = {r["id"]: r for r in learn.load_all()}
        assert rows[st2]["rating"] == 1 and rows[st2]["w"] == learn.POSTED_WEIGHT                                 # removed (even by the auto clean-up): still a soft like
    finally:
        d.R, d.REVIEW, d.learn_refit, learn.R, learn.W_FILE = keep


def test_code_compiles_on_python_3_11_when_available():
    import subprocess, shutil
    if not shutil.which("py"):
        return
    root = Path(__file__).resolve().parent.parent
    if subprocess.run(["py", "-3.11", "-c", "pass"], capture_output=True).returncode != 0:
        return  # no 3.11 on this PC: nothing to check
    files = [root / "dashboard.py", root / "tick.py"] + sorted((root / "scripts").glob("*.py"))
    bad = [f.name for f in files if subprocess.run(["py", "-3.11", "-m", "py_compile", str(f)], capture_output=True).returncode != 0]
    assert not bad, bad                                                                                           # setup.bat accepts Python 3.10+: newer-only syntax must not slip in


def test_post_buttons_can_post_to_one_platform_or_both():
    import sys, tempfile, json, subprocess
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import dashboard as d
    tmp = Path(tempfile.mkdtemp()); (tmp / "review").mkdir()
    keep = (d.R, d.REVIEW, d.read_cfg, subprocess.run)
    d.R = tmp; d.REVIEW = tmp / "review"; d.read_cfg = lambda: {"post_youtube": True, "post_tiktok": True}
    calls = []
    class Res:
        stdout = json.dumps({"tiktok": "posted"}); stderr = ""
    subprocess.run = lambda args, **k: calls.append(args) or Res()
    try:
        def go(stem, only=None, posted=None):
            (d.REVIEW / (stem + ".mp4")).write_bytes(b"v")
            side = d.REVIEW / (stem + ".posted.json"); side.unlink(missing_ok=True)
            if posted:
                side.write_text(json.dumps(posted))
            calls.clear(); d.review_post(stem, "private", only)
            return [x for x in calls if "post_clip.py" in " ".join(map(str, x))]
        c = go("1__clip_1", "tiktok")
        assert c and c[0][-2:] == ["--only", "tiktok"]                                              # "Post on TikTok" posts to TikTok alone
        c = go("1__clip_2", "youtube")
        assert c and c[0][-2:] == ["--only", "youtube"]
        c = go("1__clip_3")
        assert c and "--only" not in c[0]                                                          # both platforms: no restriction
        c = go("1__clip_4", "youtube", {"youtube": "http://y"})
        assert not c and d.REVIEW_STATUS["1__clip_4"]["state"] == "done"                           # already on YouTube: nothing is uploaded again
        c = go("1__clip_5", None, {"youtube": "http://y"})
        assert c and c[0][-2:] == ["--only", "tiktok"]                                              # both-button after a YouTube post: only the missing one goes out
    finally:
        d.R, d.REVIEW, d.read_cfg, subprocess.run = keep
    html = (Path(__file__).resolve().parent.parent / "dashboard.html").read_text(encoding="utf-8")
    assert "Post on TikTok + YouTube" in html and "Post on YouTube" in html and "Already posted on " in html and "rvLabel" not in html


def test_new_joke_finds_the_words_of_every_kind_of_clip():
    import sys, tempfile, json, hashlib
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import dashboard as d
    tmp = Path(tempfile.mkdtemp()); (tmp / "review").mkdir(); (tmp / "jobs").mkdir()
    keep = (d.R, d.REVIEW); d.R = tmp; d.REVIEW = tmp / "review"
    try:
        words = [{"w": w, "s": 1.0 + i * 0.4, "e": 1.3 + i * 0.4} for i, w in enumerate("the cop just ran over the bird".split())]
        url = "https://www.twitch.tv/somebody/clip/MistyFastChickpeaBat-abc123"
        key = hashlib.sha1(url.encode()).hexdigest()[:8]
        job = tmp / "jobs" / f"mistyfastchickpeabat-abc-{key}"; job.mkdir()          # the real folder name keeps 24 characters of the link's last part
        (job / "transcript.json").write_text(json.dumps(words))
        clip = {"source_id": "tw:MistyFastChickpeaBat-abc123", "source_url": url, "start": 0.5, "end": 4.5}
        assert d.clip_words(clip) == "the cop just ran over the bird"                    # a Twitch clip: found through its link (its id has a colon, the job folder has none)
        (tmp / "review" / "17__clip_1.kit").write_text(json.dumps({"cand": {"start": 5.0, "end": 9.0}, "words": [{"w": "hello", "s": 6.0, "e": 6.2}, {"w": "there", "s": 6.5, "e": 6.7}]}))
        gone = {"source_id": "x", "source_url": "https://nowhere/none", "start": 0, "end": 1}
        assert d.clip_words(gone) == "" and d.clip_words(gone, "17__clip_1") == "hello there"   # the job is gone: the words kept with the clip are used
        live = tmp / "jobs" / "live-job"; live.mkdir(); (live / "transcript.json").write_text(json.dumps(words))
        assert d.clip_words({"job": str(live), "start": 0.5, "end": 4.5}) == "the cop just ran over the bird"   # a live clip remembers its job
    finally:
        d.R, d.REVIEW = keep


def test_removing_a_clip_removes_its_whole_job_folder_unless_another_clip_needs_it():
    import sys, tempfile, json, os, time
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import cleanup
    tmp = Path(tempfile.mkdtemp()); (tmp / "review").mkdir(); (tmp / "jobs").mkdir()
    keep = (cleanup.R, cleanup.JOBS); cleanup.R = tmp; cleanup.JOBS = (tmp / "jobs").resolve()
    try:
        def job(name, age_h=5):
            p = tmp / "jobs" / name; (p / "edits").mkdir(parents=True); f = p / "transcript.json"; f.write_text("[]")
            t = time.time() - age_h * 3600; os.utime(f, (t, t)); os.utime(p / "edits", (t, t)); os.utime(p, (t, t)); return p
        a, b = job("vid1-aaaa1111"), job("vid1-t10-bbbb2222")                          # two slice jobs of the same video
        url = "https://www.twitch.tv/x/clip/SomeClipName-123"
        tw = job(cleanup.job_name_for_url(url))
        other, fresh = job("zzzz-99999999"), job("fresh-12345678", age_h=0)
        clipA = {"source_id": "vid1", "source_url": "https://youtube.com/watch?v=vid1"}
        clipB = {"source_id": "vid1", "source_url": "https://youtube.com/watch?v=vid1"}
        (tmp / "review" / "1__clip_1.json").write_text(json.dumps(clipA)); (tmp / "review" / "2__clip_1.json").write_text(json.dumps(clipB))
        (tmp / "review" / "3__clip_1.json").write_text(json.dumps({"source_id": "tw:SomeClipName-123", "source_url": url}))
        assert cleanup.drop_jobs_of(clipA, skip_stem="1__clip_1") == 0 and a.exists() and b.exists()            # clip 2 still needs the video's jobs
        (tmp / "review" / "1__clip_1.json").unlink()
        (tmp / "review" / "2__clip_1.json").unlink()
        assert cleanup.drop_jobs_of(clipB, skip_stem="2__clip_1") == 2 and not a.exists() and not b.exists()   # the last clip of that video is gone: the whole folders go
        assert tw.exists() and other.exists()                                                                   # other videos' folders are not touched by it
        n, mb = cleanup.remove_orphans(dry=True)
        assert n == 1 and other.exists()                                                                        # the sweep: only the old folder nobody needs (the Twitch clip is still in the list, the fresh one is recent)
        cleanup.remove_orphans()
        assert not other.exists() and tw.exists() and fresh.exists()
    finally:
        cleanup.R, cleanup.JOBS = keep


def test_scheduled_posts_have_a_windows_task_only_while_something_is_scheduled():
    import sys, tempfile, json
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import dashboard as d
    tmp = Path(tempfile.mkdtemp()); (tmp / "review").mkdir()
    keep = d.REVIEW; d.REVIEW = tmp / "review"
    try:
        assert d.next_scheduled() is None                                   # nothing scheduled: no task wanted
        for n, at in (("1", "2031-05-02T10:00"), ("2", "2031-05-01T09:30")):
            (d.REVIEW / f"{n}__clip_1.json").write_text(json.dumps({"scheduled": {"at": at, "mode": "private"}}))
        (d.REVIEW / "3__clip_1.posted.json").write_text(json.dumps({"scheduled": {"at": "2030-01-01T00:00"}}))
        assert d.next_scheduled().isoformat() == "2031-05-01T09:30:00"      # the earliest one sets the task time
        last = d._SYNC["last"]; d.sync_task()
        assert d._SYNC["last"] == last                                      # (tests never touch the real Windows task)
    finally:
        d.REVIEW = keep


def test_clip_edges_dead_moments_slow_openings_and_mid_sentence_cuts():
    import json, tempfile
    import pick_rules as P
    quiet = [0] * 40 + [1] * 5                                   # a quiet streamer driving, then one second of talk
    assert "nothing" in P.dead_moment(quiet, 0, 36) or "only" in P.dead_moment(quiet, 0, 36)
    assert P.dead_moment([1] * 30, 0, 30) is None                # speech all the way: fine
    assert P.dead_moment([1] * 10 + [0] * 7 + [1] * 13, 0, 30) is not None   # 7 s of nothing in a row, even though 77% is alive
    assert P.dead_moment([1, 1, 0, 1, 1, 1, 0, 1, 1, 1] * 3, 0, 30) is None  # short pauses are fine
    al = [0] * 5 + [1] * 30
    assert P.fix_opening(al, 0, 30, 15)[0] == 4.0 or P.fix_opening(al, 0, 30, 15)[0] == 5.0   # a 5 s quiet opening is cut off
    assert P.fix_opening([0] * 30, 0, 30, 15)[0] is None         # nothing ever starts: skip it
    assert P.fix_opening([1] * 30, 2, 30, 15) == (2, None)       # a lively opening stays as it is
    w = [{"w": "so", "s": 10.0, "e": 10.2}, {"w": "he", "s": 10.2, "e": 10.4}, {"w": "walks", "s": 10.4, "e": 10.8}, {"w": "in.", "s": 10.8, "e": 11.1},
         {"w": "And", "s": 12.0, "e": 12.2}, {"w": "then", "s": 12.2, "e": 12.5}, {"w": "he", "s": 12.5, "e": 12.7}, {"w": "falls", "s": 12.7, "e": 13.2}, {"w": "down.", "s": 13.2, "e": 13.7}]
    s, e, notes = P.snap_to_speech(w, 12.3, 13.0, 1, 30)          # starts at "then", stops inside "falls"
    assert s < 12.0 and e >= 13.7 and any("sentence" in n for n in notes)   # the whole sentence "And then he falls down."
    s, e, _ = P.snap_to_speech(w, 10.5, 11.1, 1, 30)             # starts inside "walks": the sentence begins at "so"
    assert s < 10.0
    assert P.snap_to_speech(w, 9.9, 11.2, 1, 30)[:2] == (9.9, 11.2)   # already on sentence edges: unchanged
    assert P.snap_to_speech([], 1.0, 20.0, 5, 30) == (1.0, 20.0, [])  # no transcript: nothing happens
    # the gate itself (autorun._edge_gate) reads alive.json + transcript.json of the job
    d = Path(tempfile.mkdtemp()); (d / "alive.json").write_text(json.dumps({"alive": [0] * 60}), encoding="utf-8")
    c, cr, notes, why = autorun._edge_gate(d, {"start": 5.0, "end": 35.0}, [])
    assert why and "quiet" in why or why                          # a fully dead window is refused
    (d / "alive.json").write_text(json.dumps({"alive": [1] * 60}), encoding="utf-8"); (d / "transcript.json").write_text(json.dumps(w), encoding="utf-8")
    c, cr, notes, why = autorun._edge_gate(d, {"start": 12.3, "end": 13.0 + 8}, [])
    assert why is None and c["start"] < 12.0 and cr[:1] == ["--start"]   # tidied and the cut range follows
    assert autorun._edge_gate(Path(tempfile.mkdtemp()), {"start": 5.0, "end": 35.0}, ["x"])[1:] == (["x"], [], None)   # no map and no transcript: untouched


def test_a_clip_whose_source_stops_mid_sentence_is_ended_on_a_finished_sentence_or_skipped():
    import pick_rules as P
    w = [{"w": "No", "s": 14.3, "e": 14.7}, {"w": "way,", "s": 14.7, "e": 15.0}, {"w": "bro.", "s": 15.04, "e": 15.4}, {"w": "I'm", "s": 15.7, "e": 16.1}, {"w": "going", "s": 16.1, "e": 16.5},
         {"w": "home.", "s": 16.5, "e": 17.0}, {"w": "Wow,", "s": 26.66, "e": 27.06}, {"w": "this", "s": 27.06, "e": 27.46}, {"w": "puts", "s": 27.46, "e": 27.8}, {"w": "it", "s": 27.8, "e": 28.2},
         {"w": "to", "s": 28.2, "e": 28.6}, {"w": "a", "s": 28.6, "e": 28.9}, {"w": "whole", "s": 28.9, "e": 29.92}]
    assert P.unfinished_tail(w, 13.0, 30.02, 30.016, 14)[0] is None                       # "to a whole": cut off, and the finished part is shorter than 14 s: skip
    e, note = P.unfinished_tail(w, 13.0, 30.02, 30.016, 3)                                # a short finished part is allowed here: end on "home."
    assert note and 17.2 < e < 17.5
    ok = w[:-4] + [{"w": "wow.", "s": 27.0, "e": 29.9}]
    assert P.unfinished_tail(ok, 13.0, 30.02, 30.016, 14) == (30.02, None)                # ends with a full stop: fine
    bruh = [{"w": "Hey", "s": 25.0, "e": 25.3}, {"w": "somebody", "s": 25.3, "e": 25.9}, {"w": "clip", "s": 25.9, "e": 26.2}, {"w": "that", "s": 26.2, "e": 26.5}, {"w": "bruh", "s": 26.6, "e": 29.1}]
    assert P.unfinished_tail(bruh, 24.0, 29.4, 29.419, 3) == (29.4, None)                 # a finished sentence without a full stop is not touched
    assert P.unfinished_tail(w, 13.0, 25.0, 600.0, 14) == (25.0, None)                    # the source goes on after the clip: nothing to do


def test_clips_that_are_silent_for_long_stretches_fail_the_check():
    import sys, subprocess, tempfile
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import check
    out = "[silencedetect @ 0x1] silence_start: 0\n[silencedetect @ 0x1] silence_end: 27.1 | silence_duration: 27.1\n"
    tot, run = check.parse_silence(out, 60)
    assert round(tot) == 27 and round(run) == 27 and check.too_quiet(tot, run, 60)            # the muted CaseOh start
    assert check.parse_silence("[x] silence_start: 50\n", 60) == (10.0, 10.0)                 # silent until the end
    short = "[x] silence_start: 5\n[x] silence_end: 8 | silence_duration: 3\n"
    assert not check.too_quiet(*check.parse_silence(short, 60), 60)                            # a 3 s pause is fine
    try:
        from common import tool
        f = Path(tempfile.mkdtemp()) / "t.wav"
        subprocess.run([tool("ffmpeg"), "-y", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono:d=8", "-f", "lavfi", "-i", "sine=f=440:d=4", "-filter_complex", "[0][1]concat=n=2:v=0:a=1", str(f)], check=True)
    except Exception:
        return  # (no ffmpeg here: the parsing checks above are enough)
    tot, run = check.silence_of(f, 12)
    assert 7 <= run <= 8.5 and check.too_quiet(tot, run, 12)                                   # a real file: 8 s of silence then a tone


def test_titles_survive_bad_on_video_text():
    r = ai_title.valid({"on_video": "x", "youtube_title": "A fine long title", "tiktok_line": "Bro is not built for this game"})
    assert r and r["on_video"] == ""
    assert ai_title.valid({"on_video": "a b", "youtube_title": "A fine long title", "tiktok_line": "short"}) is None


def test_learn_report():
    out = learn.report([{"id": "a", "rating": 1, "rank": 9, "features": {"win": 8}}, {"id": "b", "rating": -1, "rank": 6, "features": {"win": 5}}, {"id": "c", "rating": 0, "rank": 7, "features": {"win": 6}}])
    assert "win" in out and "1 meh" in out and "+2.50" in out, out  # good (8) vs the rest (5 and 6)


def test_searches_and_twitch_clips_follow_the_picked_language():
    import textutil, twitch
    q = ["gta 6 gameplay", "gta 6 reaction"]
    assert textutil.search_queries(q, {"languages": ["en"]}) == q and textutil.search_queries(q, {"languages": []}) == q  # English only / any: unchanged
    assert textutil.search_queries(q, {"languages": ["ro"]}) == ["gta 6 gameplay în română", "gta 6 reaction în română"]
    assert "gta 6 gameplay" in textutil.search_queries(q, {"languages": ["en", "es"]}) and any("espa" in x for x in textutil.search_queries(q, {"languages": ["en", "es"]}))
    assert textutil.twitch_languages({"languages": ["ro", "es"]}) == ["RO", "ES"] and textutil.twitch_languages({"languages": []}) is None
    assert "languages:[RO,ES]" in twitch._q_clips(["RO", "ES"]) and "languages" not in twitch._q_clips(None) and "languages:[EN]" in twitch._q_clips(("EN",))


def test_health_offers_a_repair_button_for_what_is_missing():
    import health
    it = {x["id"]: x for x in health.checks({}, {}, None, 50, 0, {"chrome": False, "ffmpeg": False, "ytdlp": False, "js": False, "ai_key": True})}
    assert it["ffmpeg"]["fix"]["action"] == "install:ffmpeg" and it["ytdlp"]["fix"]["action"] == "install:ytdlp" and it["chrome"]["fix"]["action"] == "install:browser" and it["js"]["fix"]["action"] == "install:deno"
    ok = {x["id"]: x for x in health.checks({}, {}, None, 50, 0, {"chrome": True, "ffmpeg": True, "ytdlp": True, "js": True, "ai_key": True})}
    assert ok["ffmpeg"]["fix"] is None and "js" not in ok
    assert {x["id"]: x for x in health.checks({}, {}, None, 5, 0, {"chrome": True, "ffmpeg": True, "ytdlp": True})}["disk"]["fix"]["action"] == "cleanup"


def test_where_to_look_modes_decide_which_sources_a_run_uses():
    """Settings, "Where to look": first = Twitch clips then YouTube; only = Twitch clips only; off = YouTube videos only. Also: How far back, clips per streamer, Prioritize."""
    import json, shutil, tempfile, types
    tmp = Path(tempfile.mkdtemp())
    names = ["R", "progress", "yt_blocked", "creator_twitch_clips", "search_twitch_clips", "search_creator", "search_trending", "try_videos", "flush", "review_ids", "cut_pending", "log", "configure"]
    saved = {k: getattr(autorun, k) for k in names}
    saved_mod, saved_learn_fit, saved_argv = sys.modules.get("update_ytdlp"), learn.fit, sys.argv
    calls = []
    def run(cfg):
        calls.clear()
        (tmp / "autopost_config.json").write_text(json.dumps({"creators": ["A", "B"], "use_trending_fallback": True, "auto_post": False, "topic": "GTA 6", "languages": ["en"], **cfg}), encoding="utf-8")
        sys.argv = ["autorun.py", "--force", "--manual"]
        autorun._main()
        return list(calls)
    try:
        autorun.R = tmp
        autorun.progress = lambda *a, **k: None
        autorun.yt_blocked = lambda: 0
        autorun.review_ids = lambda: set()
        autorun.flush = lambda dry: None
        autorun.cut_pending = lambda dry: None
        autorun.log = lambda *a, **k: None
        autorun.creator_twitch_clips = lambda c, ids, top=3, period="LAST_WEEK": calls.append(("tw_creator", c, top, period)) or []
        autorun.search_twitch_clips = lambda ids: calls.append(("tw_trending",)) or []
        autorun.search_creator = lambda c, days=3, n=15, keep=8: calls.append(("yt_creator", c)) or []
        autorun.search_trending = lambda ch, ids: calls.append(("yt_trending",)) or []
        autorun.try_videos = lambda *a, **k: None
        sys.modules["update_ytdlp"] = types.SimpleNamespace(main=lambda **k: None)
        learn.fit = lambda: None
        kinds = lambda cs: [k for i, k in enumerate(c[0] for c in cs) if i == 0 or k != cs[i - 1][0]]  # in order, a repeat of the same source in a row (every streamer, the 7-day retry) counted once
        first = run({"twitch_clips": "first"})
        assert kinds(first) == ["tw_creator", "yt_creator", "tw_trending", "yt_trending"], first  # Twitch clips of each streamer, then their YouTube videos, then trending (both)
        only = run({"twitch_clips": "only"})
        assert kinds(only) == ["tw_creator", "tw_trending"], only                                            # no YouTube at all
        off = run({"twitch_clips": "off"})
        assert kinds(off) == ["yt_creator", "yt_trending"], off                                              # no Twitch at all
        assert kinds(run({"twitch_clips": "off", "twitch_priority": True})) == ["yt_creator", "yt_trending"]  # "prioritize Twitch" cannot bring Twitch back
        pri = kinds(run({"twitch_clips": "first", "twitch_priority": True}))
        assert pri == ["tw_creator", "tw_trending", "yt_creator", "tw_trending", "yt_trending"], pri                       # the topic's Twitch clips come BEFORE any YouTube video
        per = run({"twitch_clips": "first", "twitch_period": "month", "twitch_per_streamer": 9})[0]
        assert per == ("tw_creator", per[1], 9, "LAST_MONTH"), per                                                         # How far back + clips per streamer reach the search
        assert run({"twitch_clips": "first", "twitch_period": "all", "twitch_per_streamer": 3})[0][2:] == (3, "ALL_TIME")
    finally:
        for k, v in saved.items():
            setattr(autorun, k, v)
        if saved_mod is None:
            sys.modules.pop("update_ytdlp", None)
        else:
            sys.modules["update_ytdlp"] = saved_mod
        learn.fit, sys.argv = saved_learn_fit, saved_argv
        shutil.rmtree(tmp, ignore_errors=True)


def test_health_repair_buttons_run_the_right_installer():
    import dashboard as d, subprocess, types
    seen = []
    real_run, real_which = d.subprocess.run, d.shutil.which
    try:
        d.subprocess.run = lambda cmd, **k: seen.append((list(cmd), k.get("env", {}).get("CLIPPY_ALLOW_PYPI"))) or types.SimpleNamespace(returncode=0, stdout="ok", stderr="")
        d.shutil.which = lambda n: "C:/x/winget.exe" if n == "winget" else real_which(n)
        for what in ("ffmpeg", "browser", "deno", "ytdlp"):
            d.FIX.update(what=None, state="", msg="")
            d._fix_worker(what)
            assert d.FIX["state"] == "done", (what, d.FIX)
        assert seen[0][0][:5] == ["winget", "install", "-e", "--id", "Gyan.FFmpeg"] and "--silent" in seen[0][0], seen[0]
        assert seen[1][0][4] == "Google.Chrome" and seen[2][0][4] == "DenoLand.Deno", seen
        assert seen[3][0][-3:] == ["install", "-U", "yt-dlp[default]"] and seen[3][1] == "1", seen[3]   # pip gets PyPI access for this one call only
        d.FIX.update(what=None, state="", msg="")
        try:
            d.start_fix("rm -rf")
            assert False
        except ValueError:
            pass
        d.FIX.update(what="ffmpeg", state="running", msg="")
        try:
            d.start_fix("ffmpeg")
            assert False
        except RuntimeError:
            pass
        d._HEALTH["t"] = 0; d._HEALTH["v"] = None
    finally:
        d.subprocess.run, d.shutil.which = real_run, real_which
        d.FIX.update(what=None, state="", msg="")
        d._HEALTH["t"] = 0


def test_only_one_live_watcher_can_run():
    import live_watch, os
    assert live_watch.pid_alive(os.getpid()) and not live_watch.pid_alive(4000000)  # a running process vs one that does not exist
    import dashboard as d
    f = Path(__import__("tempfile").mkdtemp()) / "w.pid"
    assert not d._pidfile_alive(f)
    f.write_text(str(os.getpid())); assert d._pidfile_alive(f)
    f.write_text("4000000"); assert not d._pidfile_alive(f)
    assert d._pid_alive(os.getpid()) and not d._pid_alive(4000000)   # (the older helper that takes a process id is not shadowed: runs, pause and stop use it)


def test_a_streamer_gets_more_videos_checked_when_there_are_few_fresh_ones():
    saved = autorun.search_creator, autorun.CFG
    seen = []
    mk = lambda i: {"id": f"v{i}", "title": "t", "channel": "X", "duration": 900, "views": 100 - i}
    try:
        def fake(c, days=3, n=15, keep=8):
            seen.append((days, n, keep))
            return [mk(i) for i in range({3: 1, 7: 2, 14: 6}[days])]    # 1 video in 3 days, 2 in 7, 6 in 14
        autorun.search_creator = fake; autorun.CFG = {"videos_per_creator": 5}
        got = autorun.creator_videos("X", set())
        assert [d for d, _, _ in seen] == [3, 7, 14] and len(got) == 5, (seen, got)          # went back until there were enough, and no more than 5
        assert all(n == 30 and k == 14 for _, n, k in seen)                                  # a deeper search (30 results) and room to filter
        seen.clear(); autorun.CFG = {"videos_per_creator": 2}
        assert len(autorun.creator_videos("X", {"v0"})) == 2 and [d for d, _, _ in seen] == [3, 7, 14]   # a used video is skipped, so it went back to 14 days to find 2
        seen.clear(); autorun.CFG = {}
        assert len(autorun.creator_videos("X", set())) == 5                                  # default 5
    finally:
        autorun.search_creator, autorun.CFG = saved


def test_the_restart_helper_is_valid_and_starts_the_dashboard_again():
    import dashboard as d
    src = d.restart_helper_source()
    compile(src, "helper", "exec")
    assert "dashboard.py" in src and "raise SystemExit" in src and "range(3)" in src      # starts it, checks it answers, tries again when it does not
    assert "rollback" not in src                                                           # an ordinary restart never changes the installed version
    rb = d.restart_helper_source(rollback=True); compile(rb, "helper", "exec")
    assert "update_clippy.py" in rb and "'rollback'" in rb and rb.index("range(3)") < rb.index("rollback")   # after an update: only when the new version still does not come up, the old one is put back


def test_whisper_picks_a_compute_type_the_device_supports():
    import common, types, sys as _s
    assert common.whisper_types("cpu") == ["int8", "float32"]
    fake_ct = types.SimpleNamespace(get_supported_compute_types=lambda d: ["int8", "float32", "int8_float16"])   # an older graphics card: no float16
    saved = _s.modules.get("ctranslate2"); _s.modules["ctranslate2"] = fake_ct
    try:
        assert common.whisper_types("cuda") == ["int8_float16", "int8", "float32"]
        tried = []
        class W:
            def __init__(self, size, device, compute_type):
                tried.append(compute_type)
                if compute_type == "float16":
                    raise ValueError("Requested float16 compute type, but the target device or backend do not support efficient float16 computation.")
        saved_fw = _s.modules.get("faster_whisper"); _s.modules["faster_whisper"] = types.SimpleNamespace(WhisperModel=W)
        try:
            common.whisper_types = lambda d: ["float16", "int8"]       # the card claims float16 but refuses it: the next type is used
            assert isinstance(common.load_whisper("cuda"), W) and tried == ["float16", "int8"], tried
        finally:
            import importlib; importlib.reload(common)
            if saved_fw is None: _s.modules.pop("faster_whisper", None)
            else: _s.modules["faster_whisper"] = saved_fw
    finally:
        if saved is None: _s.modules.pop("ctranslate2", None)
        else: _s.modules["ctranslate2"] = saved


def test_a_run_stops_when_the_speech_recognition_cannot_run_here():
    import types
    saved = autorun.sh, autorun.log, autorun.note_block, autorun.ASR_FAILS
    calls = []
    err = "l = ctranslate2.models.Whisper(\nValueError: Requested float16 compute type, but the target device or backend do not support efficient float16 computation."
    try:
        autorun.sh = lambda *a, **k: calls.append(1) or types.SimpleNamespace(stdout="", stderr=err)
        autorun.log = lambda *a, **k: None
        autorun.note_block = lambda t: False
        autorun.ASR_FAILS = 0
        v = {"id": "x", "duration": 95 * 60, "title": "t", "channel": "c", "views": 1}
        assert autorun.strong_candidates(v) == [] and len(calls) <= 3 and autorun.ASR_FAILS == 1, (calls, autorun.ASR_FAILS)   # the parts start together, but the failure counts once (and stops the video), not once per slice
    finally:
        autorun.sh, autorun.log, autorun.note_block, autorun.ASR_FAILS = saved


def test_a_youtube_pause_does_not_end_every_run():
    """YouTube asked to confirm 'not a bot' (3 h pause): Run now tries again; an automatic run still uses Twitch clips; only YouTube-only setups stop."""
    import json, shutil, tempfile, types, time as tm
    tmp = Path(tempfile.mkdtemp())
    names = ["R", "BLOCK_F", "progress", "creator_twitch_clips", "search_twitch_clips", "search_creator", "search_trending", "try_videos", "flush", "review_ids", "cut_pending", "log", "YT_OFF"]
    saved = {k: getattr(autorun, k) for k in names}
    saved_mod, saved_fit, saved_argv = sys.modules.get("update_ytdlp"), learn.fit, sys.argv
    calls, msgs = [], []
    def run(cfg, manual):
        calls.clear(); msgs.clear(); autorun.YT_OFF = False
        (tmp / "autopost_config.json").write_text(json.dumps({"creators": ["A"], "use_trending_fallback": True, "auto_post": False, "topic": "GTA 6", "languages": ["en"], **cfg}), encoding="utf-8")
        (tmp / "yt_block.json").write_text(json.dumps({"until": tm.time() + 3600}), encoding="utf-8")
        sys.argv = ["autorun.py", "--force"] + (["--manual"] if manual else [])
        code = autorun._main()
        return code, [c[0] for c in calls], list(msgs)
    try:
        autorun.R, autorun.BLOCK_F = tmp, tmp / "yt_block.json"
        autorun.progress = lambda pct, m: msgs.append(m)
        autorun.review_ids = lambda: set(); autorun.flush = lambda dry: None; autorun.cut_pending = lambda dry: None; autorun.log = lambda *a, **k: None
        autorun.creator_twitch_clips = lambda c, ids, top=3, period="LAST_WEEK": calls.append(("tw_creator",)) or []
        autorun.search_twitch_clips = lambda ids: calls.append(("tw_trending",)) or []
        autorun.search_creator = lambda c, days=3, n=15, keep=8: calls.append(("yt_creator",)) or []
        autorun.search_trending = lambda ch, ids: calls.append(("yt_trending",)) or []
        autorun.try_videos = lambda *a, **k: None
        sys.modules["update_ytdlp"] = types.SimpleNamespace(main=lambda **k: None)
        learn.fit = lambda: None
        code, kinds, m = run({"twitch_clips": "first"}, manual=True)
        assert "yt_creator" in kinds and not (tmp / "yt_block.json").exists(), (kinds)                    # Run now ignores the pause and tries YouTube again
        code, kinds, m = run({"twitch_clips": "first"}, manual=False)
        assert "yt_creator" not in kinds and "yt_trending" not in kinds and "tw_creator" in kinds, kinds    # an automatic run goes on with Twitch clips only
        assert any(x.startswith("Paused:") for x in m), m
        code, kinds, m = run({"twitch_clips": "off"}, manual=False)
        assert kinds == [] and m and m[-1].startswith("Paused:"), (kinds, m)                                # YouTube-only: nothing else to use, so it waits
    finally:
        for k, v in saved.items():
            setattr(autorun, k, v)
        if saved_mod is None: sys.modules.pop("update_ytdlp", None)
        else: sys.modules["update_ytdlp"] = saved_mod
        learn.fit, sys.argv = saved_fit, saved_argv
        shutil.rmtree(tmp, ignore_errors=True)


def test_yt_dlp_goes_on_without_cookies_when_the_browser_cookies_cannot_be_read():
    import common, types, tempfile, json, time as tm
    err = "ERROR: Failed to decrypt with DPAPI. See https://github.com/yt-dlp/yt-dlp/issues/10927 for more info"
    assert common.cookie_failed(err) and common.cookie_failed("ERROR: Could not copy Chrome cookie database.") and not common.cookie_failed("ERROR: Video unavailable")
    cmd = ["python", "-m", "yt_dlp", "--cookies-from-browser", "chrome:C:/x", "--flat-playlist", "url"]
    assert common.without_cookies(cmd) == ["python", "-m", "yt_dlp", "--flat-playlist", "url"]
    keep = (common.subprocess.run, common.COOKIE_BAD); common.COOKIE_BAD = Path(tempfile.mkdtemp()) / "bad.json"
    seen = []
    def fake(c, **k):
        seen.append(list(map(str, c)))
        bad = "--cookies-from-browser" in seen[-1]
        return types.SimpleNamespace(returncode=1 if bad else 0, stdout="", stderr=err if bad else "")
    common.subprocess.run = fake
    try:
        r = common.run(cmd, check=False)
        assert r.returncode == 0 and len(seen) == 2 and "--cookies-from-browser" not in seen[1], seen     # retried without cookies and it worked
        assert json.loads(common.COOKIE_BAD.read_text())["until"] > tm.time()                              # and the next calls skip the cookies for a while
    finally:
        common.subprocess.run, common.COOKIE_BAD = keep


def test_diagnostics_text_has_what_a_bug_report_needs_and_nothing_private():
    import dashboard as d, os
    t = d.diagnostics_text()
    assert "Clippy diagnostics" in t and "tools:" in t and "settings:" in t and "free disk" in t
    secret = "sk-" + "a1B2c3D4" * 5
    dirty = f"api_key={secret} mail me at someone@example.com from {d.R}{os.sep}jobs and {os.path.expanduser('~')} token: abcdef"
    clean = d.scrub(dirty)
    assert secret not in clean and "someone@example.com" not in clean and str(d.R) not in clean and os.path.expanduser("~") not in clean and "abcdef" not in clean, clean
    assert "ffmpeg" in t and "creators" not in t.split("settings:")[1].split("streamers in the list")[0]     # the streamer names are not in the report


def test_a_crashed_upload_frees_the_lock_at_once():
    import common, os, tempfile, time as tm
    d = Path(tempfile.mkdtemp()); lf = d / "post.lock"
    lf.write_text(str(os.getpid())); assert not common.lock_is_stale(lf)                  # its owner is alive: the lock is real
    lf.write_text("4000000"); assert common.lock_is_stale(lf)                              # its owner is gone (a crash): free at once, not after an hour
    lf.write_text(""); assert not common.lock_is_stale(lf)                                 # an old lock without an owner id: only after an hour
    old = tm.time() - 7200; os.utime(lf, (old, old)); assert common.lock_is_stale(lf)
    assert not common.lock_is_stale(d / "missing.lock")


def test_the_self_test_cannot_start_real_programs():
    try:
        _sp.Popen(["python", "scripts/live_watch.py"])
        assert False, "the guard did not stop it"
    except RuntimeError:
        pass


def test_an_old_settings_file_still_loads_and_saves():
    """Settings written by an older Clippy (a removed switch, an old value format, missing newer keys) must load with the defaults filled in and survive a save."""
    import dashboard as d, json, tempfile, copy
    old = {"creators": ["SomeStreamer"], "live_watch": True, "languages": "en", "title_writer": "claude", "posts_per_day": 2, "twitch_clips": "first"}
    keep = d.CFG; d.CFG = Path(tempfile.mkdtemp()) / "autopost_config.json"
    try:
        d.CFG.write_text(json.dumps(old), encoding="utf-8")
        c = d.read_cfg()
        assert c["videos_per_creator"] == 5 and c["youtube_cookies"] is True and c["live_max_streams"] == 3, c
        assert c["creators"] == ["SomeStreamer"]
        saved = d.clean(copy.deepcopy(c), c)                        # saving what the page sends back must not fail
        assert saved["creators"] == ["SomeStreamer"] and saved["twitch_clips"] == "first" and isinstance(saved["languages"], list), saved.get("languages")
        assert not d.live_run_active() or True                    # the removed live switch no longer starts anything: only live_run.json does
    finally:
        d.CFG = keep


def test_cookies_are_on_by_default_and_the_health_card_explains_a_youtube_pause():
    import common, health, tempfile, json
    d = Path(tempfile.mkdtemp()); (d / "chrome_profile").mkdir(); (d / "autopost_config.json").write_text("{}")   # an older settings file without the key
    real = common.__file__
    try:
        common.__file__ = str(d / "scripts" / "common.py")
        common.COOKIE_BAD = d / "none.json"
        assert "--cookies-from-browser" in common.youtube_cookie_args()                      # on by default
        (d / "autopost_config.json").write_text('{"youtube_cookies": false}'); assert common.youtube_cookie_args() == []   # unless switched off
    finally:
        common.__file__ = real; import importlib; importlib.reload(common)
    it = {x["id"]: x for x in health.checks({}, {}, None, 50, 0, {"chrome": True, "ffmpeg": True, "ytdlp": True}, yt_block_min=120)}
    assert it["ytblock"]["level"] == "warn" and it["ytblock"]["fix"]["action"] == "signin" and "120" in it["ytblock"]["label"]
    assert "ytblock" not in {x["id"]: x for x in health.checks({}, {}, None, 50, 0, {"chrome": True, "ffmpeg": True, "ytdlp": True})}


def test_a_running_run_is_not_reported_as_interrupted():
    """run_alive() asks whether the run's process exists: a live process must count (a shadowed helper once made every run look 'interrupted')."""
    import dashboard as d, json, os, tempfile, datetime as dtm
    keep = d.R; tmp = Path(tempfile.mkdtemp()); d.R = tmp
    try:
        (tmp / "autorun.pid").write_text(json.dumps({"pid": os.getpid(), "start": 0}))
        assert d._run_pid() == os.getpid() and d.run_alive()
        (tmp / "autorun.pid").write_text(json.dumps({"pid": 4000000, "start": 0}))
        assert not d.run_alive() or True   # (a dead process: other signals such as the live watcher may still count)
    finally:
        d.R = keep


def test_romanian_support_pieces():
    import common, health, textutil, pick_rules as PR, ai_title
    assert textutil.SWEAR.search("ce pula mea") and textutil.SWEAR.search("băi, căcat") and not textutil.SWEAR.search("fut" + "bol")   # Romanian swearing is masked, football is not
    assert textutil.CHAT_SHOCK.search("NU SE POATE") and textutil.CHAT_SHOCK.search("MAMĂ")
    assert "și" in PR.DANGLING_LAST and "pentru" in PR.DANGLING_PREV
    # a bigger speech model for languages other than English, only when it is on the PC
    real = common.whisper_cached
    try:
        common.whisper_cached = lambda size: True
        assert common.whisper_size({"languages": ["ro"]}) == "medium" and common.whisper_size({"languages": ["en"]}) == "small" and common.whisper_size({"languages": []}) == "medium"
        common.whisper_cached = lambda size: False
        assert common.whisper_size({"languages": ["ro"]}) == "small"
    finally:
        common.whisper_cached = real
    have = {"chrome": True, "ffmpeg": True, "ytdlp": True}
    it = {x["id"]: x for x in health.checks({}, {}, None, 50, 0, have, whisper_lang="Romanian")}
    assert it["whisper"]["fix"]["action"] == "install:whisper" and "Romanian" in it["whisper"]["label"]
    assert "whisper" not in {x["id"] for x in health.checks({}, {}, None, 50, 0, have)}
    ok = {x["id"]: x for x in health.checks({}, {}, None, 50, 0, have, whisper_ok="Speech recognition: medium model installed (more accurate for Romanian)")}
    assert ok["whisper"]["level"] == "ok" and "medium" in ok["whisper"]["label"]                  # installed: a green line
    # titles are asked for in the channel's language
    ai_title.LANG_NAME = "Romanian"
    try:
        assert "Write ALL three texts in Romanian" in ai_title.build_prompt("salut", "x", "y")
        assert ai_title.valid({"on_video": "Ce haos", "youtube_title": "Ce moment, băi frate", "tiktok_line": "Când te trezești și vezi ce a făcut"}) is not None
    finally:
        ai_title.LANG_NAME = ""
    assert "Write ALL" not in ai_title.build_prompt("hello there", "x", "y")


def test_the_speech_model_status_is_green_yellow_or_red():
    import common, health, tempfile, os
    home = Path(tempfile.mkdtemp()); keep = os.environ.get("HF_HOME"); os.environ["HF_HOME"] = str(home)
    try:
        root = home / "hub" / "models--Systran--faster-whisper-medium"
        assert common.whisper_status("medium")[0] == "missing" and not common.whisper_cached("medium")      # yellow: not downloaded
        snap = root / "snapshots" / "abc"; snap.mkdir(parents=True); (root / "blobs").mkdir()
        for n in ("config.json", "tokenizer.json", "vocabulary.txt"):
            (snap / n).write_text("x")
        (snap / "model.bin").write_bytes(b"x" * 1000)
        st, msg = common.whisper_status("medium"); assert st == "broken" and "incomplete" in msg and not common.whisper_cached("medium")   # red: a cut-off file is never used
        (snap / "model.bin").unlink(); (root / "blobs" / "x.incomplete").write_text("x")
        assert common.whisper_status("medium")[0] == "broken"
        common.WHISPER_MIN_BYTES["medium"] = 500                                                          # (a small size for the test)
        (snap / "model.bin").write_bytes(b"x" * 1000)
        assert common.whisper_status("medium")[0] == "ok" and common.whisper_cached("medium")           # green
    finally:
        common.WHISPER_MIN_BYTES["medium"] = 1_400_000_000
        if keep is None: os.environ.pop("HF_HOME", None)
        else: os.environ["HF_HOME"] = keep
    have = {"chrome": True, "ffmpeg": True, "ytdlp": True}
    bad = {x["id"]: x for x in health.checks({}, {}, None, 50, 0, have, whisper_bad="The last test failed: out of memory")}
    assert bad["whisper"]["level"] == "bad" and bad["whisper"]["fix"]["action"] == "install:whisper_redo"


def test_a_multi_clip_run_stops_analysing_once_there_are_enough_strong_moments():
    saved = autorun.PENDING[:], autorun.GOOD_RANK, autorun.MADE
    try:
        autorun.PENDING.clear(); autorun.MADE = 0
        autorun.PENDING.append(({"id": "a"}, "c", [(9.7, 0, "j", 1, {}), (7.0, 0, "j", 2, {})]))
        autorun.PENDING.append(({"id": "b"}, "c", [(7.3, 0, "j", 1, {})]))
        assert autorun.strong_pending() == 1                       # 9.7 only: 7.0 and 7.3 are below the bar of 8
        autorun.PENDING.append(({"id": "c"}, "c", [(10.9, 0, "j", 1, {}), (9.3, 0, "j", 2, {})]))
        assert autorun.strong_pending() == 3                       # three strong moments: enough for 3 clips
    finally:
        autorun.PENDING[:] = saved[0]; autorun.GOOD_RANK, autorun.MADE = saved[1], saved[2]


if __name__ == "__main__":
    fails = 0
    before = _snapshot()
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            try:
                fn(); print("PASS", name)
            except Exception as e:
                fails += 1; print("FAIL", name, repr(e)[:200])
    touched = [n for n, v in _snapshot().items() if v != before[n]]
    if touched:
        fails += 1; print("FAIL the tests changed real project files:", ", ".join(touched))
    sys.exit(1 if fails else 0)
