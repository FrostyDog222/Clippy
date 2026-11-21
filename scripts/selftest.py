"""Quick self-check of Clippy's selection logic (no network, no video, nothing is posted or written).  python scripts/selftest.py"""
import datetime as dt, os, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("PYTHONUTF8", "1")
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
    assert good and abs(good["rank"] - (7.5 + 1.8 + 0.5)) < 0.01 and not good["weak"], good  # AI scores + energy + streamer-reacts bonus
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
        assert [(r["id"], r["rating"], r.get("removed")) for r in rows] == [(meh, 0, True)], rows  # only the unrated, unposted clip with signals
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
    now = dtm.datetime(2026, 11, 22, 18, 0)
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
    now = dtm.datetime(2026, 11, 22, 8, 0)
    taken = [dtm.datetime(2026, 11, 22, 9, 0), dtm.datetime(2026, 11, 22, 11, 0)]
    sl = d.plan_slots(now, 5, 3, "09:00", "22:30", 120, taken)
    assert len(sl) == 5 and sl[0] == dtm.datetime(2026, 11, 22, 13, 0) and sl[1].date() == dtm.date(2026, 11, 23), sl  # 1 slot left today (limit 3), then a new day
    assert all(b - a >= dtm.timedelta(minutes=120) for a, b in zip(sl, sl[1:]) if a.date() == b.date())
    assert all(dtm.time(9, 0) <= x.time() <= dtm.time(22, 30) for x in sl)
    # spreading clips over days only takes the liked (thumbs-up) ones, best rank first
    tmp2 = Path(tempfile.mkdtemp()); keep = d.REVIEW; d.REVIEW = tmp2
    try:
        for i, (rk, rt) in enumerate([(5, 1), (9, 1), (8, 0), (7, -1)]):
            st = f"17960000{i:02d}__clip_1"; (tmp2 / f"{st}.mp4").write_bytes(b"x"); (tmp2 / f"{st}.json").write_text(json.dumps({"rank": rk, "rating": rt}))
        assert d.review_autoschedule("private", now=dtm.datetime(2026, 11, 22, 10, 0)) == 2
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
        n = d.review_autoschedule("private", now=dtm.datetime(2026, 11, 22, 10, 0), ids={"1796000000__clip_1"})
        sch = {f.stem[8:10]: json.loads(f.read_text()).get("scheduled") for f in d.REVIEW.glob("*.json")}
        assert n == 1 and sch["00"] and not sch["01"] and not sch["02"], sch                # only the selected clip, liked or not
    finally:
        d.REVIEW = keep


def test_titles_survive_bad_on_video_text():
    r = ai_title.valid({"on_video": "x", "youtube_title": "A fine long title", "tiktok_line": "Bro is not built for this game"})
    assert r and r["on_video"] == ""
    assert ai_title.valid({"on_video": "a b", "youtube_title": "A fine long title", "tiktok_line": "short"}) is None


def test_learn_report():
    out = learn.report([{"id": "a", "rating": 1, "rank": 9, "features": {"win": 8}}, {"id": "b", "rating": -1, "rank": 6, "features": {"win": 5}}, {"id": "c", "rating": 0, "rank": 7, "features": {"win": 6}}])
    assert "win" in out and "1 meh" in out and "+2.50" in out, out  # good (8) vs the rest (5 and 6)


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            try:
                fn(); print("PASS", name)
            except Exception as e:
                fails += 1; print("FAIL", name, repr(e)[:200])
    sys.exit(1 if fails else 0)
