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
    keep = (ai_check.refine_window, ai_check.analyze, autorun.log, autorun.CFG, autorun.LIMITS, autorun.ORIGIN)
    try:
        _judge_rules(json, tempfile)
    finally:
        ai_check.refine_window, ai_check.analyze, autorun.log, autorun.CFG, autorun.LIMITS, autorun.ORIGIN = keep


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
