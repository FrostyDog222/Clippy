"""Free disk space: delete downloaded source videos once their clips exist, and old finished clips. Only touches files inside this project's jobs\\ folder.
python scripts/cleanup.py [--dry]   (also runs automatically after each autorun; dashboard has a "Clean up now" button)
Rules (autopost_config.json): cleanup_sources (default true), keep_clips_days (default 3; 0 = delete clips right after the run).
 * source.mp4 / .part / audio.wav / motion.npy of a job -> deleted once the job has at least one cut clip, or it is a rejected source older than 1 hour
 * finished clips (edits\\*.mp4, *.ass) -> deleted after keep_clips_days; small json/md files (candidates, transcript, meta) are kept
 * nothing in a job touched in the last 30 minutes is deleted (it may be running)"""
import json, pathlib, sys, time

R = pathlib.Path(__file__).resolve().parent.parent
JOBS = (R / "jobs").resolve()


def size_mb(paths):
    return sum(p.stat().st_size for p in paths if p.exists()) / 1e6


def jobs_mb():
    return round(sum(p.stat().st_size for p in JOBS.rglob("*") if p.is_file()) / 1e6) if JOBS.exists() else 0


def safe(p):
    p = p.resolve()
    return JOBS in p.parents


def run(dry=False, age_min=30):
    cfg = json.loads((R / "autopost_config.json").read_text(encoding="utf-8")) if (R / "autopost_config.json").exists() else {}
    if not cfg.get("cleanup_sources", True):
        return 0, 0
    keep_days = float(cfg.get("keep_clips_days", 3))
    now = time.time(); freed = 0.0; n = 0
    if not JOBS.exists():
        return 0, 0
    for job in sorted(p for p in JOBS.iterdir() if p.is_dir()):
        files = [f for f in job.rglob("*") if f.is_file()]
        if not files or now - max(f.stat().st_mtime for f in files) < age_min * 60:
            continue
        edits = job / "edits"
        clips = list(edits.glob("clip_*.mp4")) if edits.exists() else []
        victims = []
        big = [job / "source.mp4", job / "audio.wav", job / "motion.npy", *job.glob("source*.part"), *job.glob("source.f*"), *job.glob("chat*.live_chat.json*")]
        if clips or now - max(f.stat().st_mtime for f in files) > 2400:  # clips exist, or a rejected/abandoned source (idle 40+ min)
            victims += [p for p in big if p.exists()]
        live = job / "live"  # live mode leftovers (recorded 60 s segments, chat dumps, spike clips) are only needed for minutes; the job is already idle 30+ min here
        if live.exists():
            victims += [*live.glob("seg_*.ts"), *live.glob("chat.live_chat.json*"), live / "chat.twitch.jsonl", *live.glob("*.mp4")]
        if clips and now - max(c.stat().st_mtime for c in clips) > keep_days * 86400:
            victims += clips + list(edits.glob("*.ass"))
        for v in victims:
            if not v.exists() or not safe(v):
                continue
            freed += v.stat().st_size / 1e6; n += 1
            if not dry:
                try:
                    v.unlink()
                except OSError:
                    pass
    return n, round(freed)


if __name__ == "__main__":
    dry = "--dry" in sys.argv
    age = int(sys.argv[sys.argv.index("--age") + 1]) if "--age" in sys.argv else 30  # minutes a job must be idle (testing: --age 0)
    n, mb = run(dry, age)
    print(f"{'would delete' if dry else 'deleted'} {n} file(s), {mb} MB; jobs folder now {jobs_mb()} MB")
