"""Live watcher: records several GTA 6 live streams (YouTube + Twitch) at once, reads their live chat, and when chat
explodes (WTF / OMG / NO WAY ...) cuts that moment, turns it into a Short and posts it (same rules/limits as autorun.py).
Switched on from the dashboard ("Live chat watching"). The dashboard starts it; it exits by itself when the switch is off.

python scripts/live_watch.py [--list] [--dry]    --list: only show which streams it would watch; --dry: cut clips but do not post
Sources: YouTube and Twitch live streams. One upload at a time (post.lock is shared with autorun.py)."""
import argparse, datetime as dt, json, os, pathlib, re, shutil, subprocess, sys, time

import os as _os
_os.environ["PYTHONUTF8"] = "1"  # every child process this script starts then writes UTF-8 too: a video title with an emoji must never crash a run (the old Windows text encoding cannot hold it)
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

R = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(R)); sys.path.insert(0, str(R / "scripts"))
import autorun, streams, tick  # noqa: E402
from common import job_dir  # noqa: E402

PY = autorun.PY
NOWIN = 0x08000000
STATUS = R / "live_status.json"
SESSION_MIN = 180        # one recording session per stream, then it is picked up again if still live
DISCOVER_EVERY = 300     # seconds between searches for new live streams
SEG_KEEP = 15 * 60       # delete recorded 60 s segments older than this (a clip only needs the last minute)
QUEUE_KEEP = 15 * 60     # a chat spike not used within 15 min is dropped
norm = autorun.norm


def log(m):
    print(time.strftime("%H:%M:%S ") + m, flush=True)


def cfg_now():
    return json.loads((R / "autopost_config.json").read_text(encoding="utf-8"))


def discover(cfg):
    """Live streams on the dashboard topic (or just the most watched when no topic): the user's list first (when live), then the most watched. Returns dicts with platform/channel/title/url/viewers/creator."""
    import topic as topic_mod
    tp = topic_mod.info(cfg); found = []
    for attempt in (1, 2):  # the YouTube search sometimes comes back empty under load: try once more
        try:
            yt = streams.youtube(tp["live_query"] + " live", 15)
        except BaseException as e:  # yt-dlp helpers call sys.exit on failure
            log(f"youtube search failed: {e}"); yt = []
        if yt:
            found += yt; break
        time.sleep(5)
    else:
        log("no YouTube live streams found")
    try:
        if not tp["any"]:
            found += streams.twitch("Grand Theft Auto VI" if tp["gta"] else tp["name"])
    except Exception as e:
        log(f"twitch search failed: {e}")
    creators, out = list(cfg.get("creators") or []), []
    for s in found:
        s["title"] = re.sub(r"\s*\d{4}-\d{2}-\d{2} \d{2}:\d{2}$", "", s.get("title") or "")  # yt-dlp appends the start time to live titles
        t, ch = s["title"], s.get("channel") or ""
        if re.search(r"no commentary|giveaways?|drops? giveaway", t, re.I):  # nobody talking / promo streams: no reactions to clip
            continue
        if s["platform"] == "YouTube" and not tp["matches"](t):
            continue
        if autorun.BANNED.search(t):
            continue
        letters = [c for c in t if c.isalpha()]
        if autorun.textutil.allowed_languages(cfg) == ["en"] and letters and sum(c.isascii() for c in letters) / len(letters) < 0.85:
            continue
        mine = next((c for c in creators if norm(c) in norm(ch) or (norm(ch) and norm(ch) in norm(c))), None)
        out.append({**s, "creator": mine})
    return sorted(out, key=lambda s: (s["creator"] is None, -(s.get("viewers") or 0)))


class Watch:
    def __init__(self, s):
        self.s, self.url, self.t0 = s, s["url"], time.time()
        self.dir = job_dir(self.url) / "live"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.logf = open(self.dir / "watch.log", "w", encoding="utf-8")
        self.p = subprocess.Popen([PY, "scripts/live.py", self.url, "--minutes", str(SESSION_MIN), "--before", "25", "--viewers", str(int(self.s.get("viewers") or 0))], cwd=R, stdout=self.logf, stderr=subprocess.STDOUT,
                                  env={**os.environ, "CLIP_DESK_HOME": str(R), "PYTHONIOENCODING": "utf-8"}, creationflags=NOWIN)
        self.seen, self.spikes = {f.name for f in self.dir.glob("spike_*.mp4")}, 0  # spike clips left from an earlier session are stale: ignore them
        self.rates = []  # chat rates of this stream's spikes so far: a spike is judged against its own stream's usual level, not against a bigger stream

    def alive(self):
        return self.p.poll() is None

    def stop(self):
        if self.alive():  # kill the whole tree: live.py's ffmpeg recorder and yt-dlp chat reader would otherwise keep running
            subprocess.run(["taskkill", "/PID", str(self.p.pid), "/T", "/F"], capture_output=True, creationflags=NOWIN)
        self.logf.close()

    def trim(self):
        """Keep disk small: old 60 s segments are not needed once their spike window has passed."""
        for f in self.dir.glob("seg_*.ts"):
            try:
                if time.time() - f.stat().st_mtime > SEG_KEEP:
                    f.unlink()
            except OSError:
                pass

    def new_spikes(self):
        rates = {}
        md = self.dir / "spikes.md"
        if md.exists():
            for ln in md.read_text(encoding="utf-8", errors="ignore").splitlines():
                m = re.search(r"\|\s*(\d+)\s*\|[^|]*\|\s*(spike_\d+\.mp4)\s*\|", ln)
                if m:
                    rates[m.group(2)] = int(m.group(1))
        out = []
        for f in sorted(self.dir.glob("spike_*.mp4")):
            if f.name not in self.seen and f.stat().st_size > 0 and time.time() - f.stat().st_mtime > 3:
                self.seen.add(f.name); self.spikes += 1
                r = rates.get(f.name, 0); self.rates.append(r)
                base = sorted(self.rates)[len(self.rates) // 2] or 1  # the median spike of this stream so far (the first one counts as 1.0)
                out.append({"path": f, "rate": r, "rel": r / base, "born": time.time(), "s": self.s})
        return out


def process(item, cfg, dry):
    """Spike clip -> moments.py (best moment inside it) -> cut/check/post via autorun.try_candidate. True when posted/dry-run done."""
    s, src = item["s"], item["path"]
    try:
        autorun.CHAT_HINT = json.loads(src.with_suffix(".json").read_text(encoding="utf-8")).get("sample") or None
    except Exception:
        autorun.CHAT_HINT = None
    safe = re.sub(r"[^a-z0-9]+", "-", (s["channel"] or "live").lower()).strip("-")[:20]
    clip = src.with_name(f"{safe}-{int(item['born'])}.mp4")  # moments/job names come from the file name: make it unique per stream
    src.rename(clip)
    L0, L1 = autorun.LIMITS
    r = autorun.sh([PY, "scripts/moments.py", str(clip), "--top", "3", "--min", str(L0), "--max", str(L1), "--pre", "5"], timeout=1500)
    m = re.search(r"job: (.+)", r.stdout)
    if not m:
        log("  moments failed: " + (r.stderr or r.stdout)[-200:]); return False
    job = pathlib.Path(m.group(1).strip())
    (job / "info.json").write_text(json.dumps({"title": s["title"], "channel": s["channel"], "uploader": s["channel"], "webpage_url": s["url"],
                                               "id": safe}, ensure_ascii=False), encoding="utf-8")  # credit/link in the description point at the stream
    cj = json.loads((job / "candidates.json").read_text(encoding="utf-8"))
    cj["info"] = {"title": s["title"], "channel": s["channel"], "uploader": s["channel"], "webpage_url": s["url"], "id": safe}  # cut.py takes credit/link from here
    (job / "candidates.json").write_text(json.dumps(cj, ensure_ascii=False, indent=1), encoding="utf-8")
    cands = sorted(cj["candidates"], key=lambda c: -c["score"])
    v = {"id": f"{safe}-{int(item['born'])}", "title": s["title"], "channel": s["channel"], "duration": 0, "views": s.get("viewers") or 0}
    for c in cands[:2]:  # chat already proved this moment matters, so no loudness gate here; the layout/face checks still apply
        code = autorun.try_candidate(v, c["score"], job, c["n"], c, s.get("creator"), dry)
        if code is not None:
            return code == 0
    log("  nothing usable in this spike")
    return False


def write_progress(watches, posted, want, last, prepare_only, done=False):
    """The dashboard's Current run bar: what the live watcher is doing right now (so the run never looks 'interrupted' while it is just waiting for chat to explode)."""
    try:
        if done:
            pct, msg = 100, f"Done: {posted} clip(s) are in Ready to post"
        else:
            pct = 5 + int(90 * min(posted, want) / want) if prepare_only else 5
            msg = f"Live: watching {len(watches)} stream(s)" + (f" - {posted} of {want} clip(s) ready" if prepare_only else f" - {posted} posted") + ("" if last in ("starting", "") else f" - {last}")
        (R / "progress.json").write_text(json.dumps({"pct": pct, "msg": msg[:200], "ts": dt.datetime.now().isoformat(timespec="seconds")}), encoding="utf-8")
    except Exception:
        pass


def write_status(watches, queue, posted, last):
    STATUS.write_text(json.dumps({"ts": dt.datetime.now().isoformat(timespec="seconds"), "queued": len(queue), "posted": posted, "last": last,
                                  "watching": [{"platform": w.s["platform"], "channel": w.s["channel"], "title": w.s["title"][:80], "url": w.url,
                                                "viewers": w.s.get("viewers"), "since": int(w.t0), "spikes": w.spikes} for w in watches]}), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--list", action="store_true"); ap.add_argument("--dry", action="store_true")
    ap.add_argument("--force", action="store_true", help="test: ignore daily limit / window / gap")
    a = ap.parse_args()
    cfg = cfg_now()
    if a.list:
        for s in discover(cfg):
            print(f"{s['platform']:8} {s.get('viewers') or '':>7}  {'*' if s['creator'] else ' '} {s['channel'][:24]:24} {s['title'][:60]}")
        return 0
    autorun.configure(cfg); autorun.ORIGIN = "live"
    try:  # same rule as a normal run: yt-dlp is checked for updates before the watcher starts
        import update_ytdlp
        update_ytdlp.main(before_run=True)
    except BaseException as e:
        log(f"yt-dlp update check skipped ({str(e)[:60]})")
    watches, queue, cooldown, posted, last, next_find = [], [], {}, 0, "starting", 0.0
    made_by = {}  # prepare-only: clips made per streamer in this session
    log("live watcher started")
    try:
        while True:
            cfg = cfg_now()
            if not cfg.get("live_watch") or (R / "PAUSED.txt").exists():
                log("switched off or paused: stopping"); break
            autorun.configure(cfg)
            maxn = max(1, min(5, int(cfg.get("live_max_streams", 3))))
            for w in [w for w in watches if not w.alive()]:
                watches.remove(w); w.stop(); log(f"stream finished: {w.s['channel']}")
                # died early (Twitch ad breaks can break the recorder) -> retry soon; ran a long time -> stream probably ended
                cooldown[w.url] = time.time() + (30 if time.time() - w.t0 < 1800 else 600); next_find = 0.0
            while len(watches) > maxn:  # user lowered the number
                w = watches.pop(); w.stop()
            if len(watches) < maxn and time.time() >= next_find:
                next_find = time.time() + DISCOVER_EVERY
                have = {w.url for w in watches}
                for s in discover(cfg):
                    if len(watches) >= maxn:
                        break
                    if s["url"] in have or cooldown.get(s["url"], 0) > time.time():
                        continue
                    log(f"watching {s['platform']} {s['channel']} ({s.get('viewers')} viewers): {s['title'][:60]}")
                    watches.append(Watch(s)); have.add(s["url"])
            for w in watches:
                w.trim()
                for sp in w.new_spikes():
                    log(f"chat spike on {w.s['channel']} ({sp['rate']} msgs / 10 s)"); queue.append(sp)
            queue[:] = [q for q in queue if time.time() - q["born"] < QUEUE_KEEP and q["path"].exists()]
            if queue:
                st = json.loads((R / "autopost_state.json").read_text(encoding="utf-8")) if (R / "autopost_state.json").exists() else {"posts": []}
                ok, why, _ = tick.decide(cfg, st.get("posts", []), dt.datetime.now(), False, gap=bool(cfg.get("auto_post")))  # same posting window and daily limit as everything else; the gap only matters when it posts by itself
                by = cfg.get("live_by") or ("auto" if (cfg.get("autopilot") or cfg.get("auto_post")) else "manual")
                ok = ok or a.force or by == "manual"  # the schedule (window, daily limit, gap) only applies to automation (started by Autopilot); a live run you started yourself ignores it
                if ok:
                    today = dt.datetime.now().strftime("%Y-%m-%d")
                    cnt = {}
                    for p in st.get("posts", []):
                        if p.get("date") == today and not p.get("deleted"):
                            cnt[norm(p.get("channel") or "")] = cnt.get(norm(p.get("channel") or ""), 0) + 1
                    for k_, n_ in made_by.items():
                        cnt[k_] = cnt.get(k_, 0) + n_
                    maxc = 10 ** 6 if by == "manual" else int(cfg.get("max_per_creator_per_day", 1))  # the per-streamer daily limit is an Autopilot rule: a live run you started yourself ignores it
                    queue.sort(key=lambda q: (-q.get("rel", 1.0), -q["rate"]))  # relative to each stream's own usual spikes, so a small stream's real peak is not buried by a big stream's normal chat
                    pick = next((q for q in queue if cnt.get(norm(q["s"]["channel"]), 0) < maxc), None)
                    if pick:
                        queue.remove(pick)
                        last = f"processing spike from {pick['s']['channel']}"; write_status(watches, queue, posted, last)
                        try:
                            if process(pick, cfg, a.dry):
                                posted += 1; last = (f"posted a clip from {pick['s']['channel']}" if cfg.get("auto_post", False) else f"clip ready from {pick['s']['channel']} (in Ready to post)")
                                made_by[norm(pick["s"]["channel"])] = made_by.get(norm(pick["s"]["channel"]), 0) + 1
                            else:
                                last = f"spike from {pick['s']['channel']} was not good enough"
                        except Exception as e:
                            last = f"error: {e}"; log(last)
                        for f in pick["path"].parent.glob(f"{re.sub(r'[^a-z0-9]+', '-', (pick['s']['channel'] or 'live').lower()).strip('-')[:20]}-{int(pick['born'])}.*"):
                            f.unlink(missing_ok=True)  # the spike clip is no longer needed (the finished Short is kept by cleanup.py rules)
                        log(last)
            write_status(watches, queue, posted, last)
            want = max(1, int(cfg.get("live_target") or cfg.get("clips_per_run", 1) or 1))
            write_progress(watches, posted, want, last, not cfg.get("auto_post", False))
            if not cfg.get("auto_post", False) and posted >= want:  # prepare-only live mode: "clips this run" is how many clips it makes, then it switches itself off
                log(f"{posted} clip(s) ready (the number you chose for this run): live mode switches itself off")
                c2 = cfg_now(); c2["live_watch"] = False
                (R / "autopost_config.json").write_text(json.dumps(c2, indent=2), encoding="utf-8")
                last = f"done: {posted} clip(s) are in Ready to post"; write_progress(watches, posted, want, last, True, done=True); break
            time.sleep(10)
    finally:
        for w in watches:
            w.stop()
            for f in list(w.dir.glob("seg_*.ts")) + [w.dir / "segments.csv"]:
                f.unlink(missing_ok=True)
        write_status([], [], posted, "stopped")
        log("live watcher stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
