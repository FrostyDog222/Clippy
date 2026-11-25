"""Free disk space: delete downloaded source videos once their clips exist, and old finished clips. Only touches files inside this project's jobs\\ folder.
python scripts/cleanup.py [--dry]   (also runs automatically after each autorun; dashboard has a "Clean up now" button)
Rules (autopost_config.json): cleanup_sources (default true), keep_clips_days (default 3; 0 = delete clips right after the run).
 * source.mp4 / .part / audio.wav / motion.npy of a job -> deleted once the job has at least one cut clip, or it is a rejected source older than 1 hour
 * finished clips (edits\\*.mp4, *.ass) -> deleted after keep_clips_days; small json/md files (candidates, transcript, meta) are kept
 * nothing in a job touched in the last 30 minutes is deleted (it may be running)"""
import hashlib, json, pathlib, re, shutil, sys, time

R = pathlib.Path(__file__).resolve().parent.parent
JOBS = (R / "jobs").resolve()


def size_mb(paths):
    return sum(p.stat().st_size for p in paths if p.exists()) / 1e6


def jobs_mb():
    return round(sum(p.stat().st_size for p in JOBS.rglob("*") if p.is_file()) / 1e6) if JOBS.exists() else 0


def safe(p):
    p = p.resolve()
    return JOBS in p.parents


def job_name_for_url(url):
    """The job folder name Clippy gives a source link (the same rule as common.job_dir), lower case."""
    stem = re.sub(r"[^a-z0-9]+", "-", str(url).split("v=")[-1].split("/")[-1].lower())[:24].strip("-")
    return f"{stem or 'job'}-{hashlib.sha1(str(url).encode()).hexdigest()[:8]}"


def _clip_refs(d):
    """(exact job folder names, name prefixes) that one clip in Ready to post still needs: its own job, its source link's job, and every slice job of its source video."""
    names, prefixes = set(), set()
    if d.get("job"):
        names.add(pathlib.Path(d["job"]).name.lower())
    if d.get("source_url"):
        names.add(job_name_for_url(d["source_url"]).lower())
    if d.get("source_id"):
        prefixes.add(str(d["source_id"]).lower() + "-")
    return names, prefixes


def referenced(skip_stem=None):
    """Everything the clips waiting in Ready to post still need: (names, prefixes). skip_stem = one clip that is being removed right now."""
    names, prefixes = set(), set()
    rv = R / "review"
    for f in (rv.glob("*__clip_*.json") if rv.exists() else []):
        if f.name.endswith(".posted.json") or f.stem == skip_stem:
            continue
        try:
            n, p = _clip_refs(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            continue
        names |= n; prefixes |= p
    return names, prefixes


def is_referenced(name, refs):
    names, prefixes = refs
    n = name.lower()
    return n in names or any(n.startswith(p) for p in prefixes)


def drop_jobs_of(d, skip_stem=None):
    """A clip was removed for good: delete the whole job folder(s) it came from (transcript, candidates, everything), unless another clip waiting in Ready to post still uses them.
    Returns the number of folders deleted."""
    refs = referenced(skip_stem)
    names, prefixes = _clip_refs(d)
    found = []
    if JOBS.exists():
        for j in JOBS.iterdir():
            if j.is_dir() and (j.name.lower() in names or any(j.name.lower().startswith(p) for p in prefixes)):
                found.append(j)
    n = 0
    for j in found:
        if is_referenced(j.name, refs) or not safe(j):
            continue
        shutil.rmtree(j, ignore_errors=True); n += 1
    return n


def remove_orphans(dry=False, idle_min=120):
    """Job folders that no clip waiting in Ready to post needs any more and that nothing touched for idle_min minutes: deleted whole (the finished clips are in review\\)."""
    if not JOBS.exists():
        return 0, 0
    refs, now, n, freed = referenced(), time.time(), 0, 0.0
    for j in sorted(p for p in JOBS.iterdir() if p.is_dir()):
        if is_referenced(j.name, refs) or not safe(j):
            continue
        try:
            files = [f for f in j.rglob("*") if f.is_file()]
            last = max([j.stat().st_mtime] + [f.stat().st_mtime for f in files])
            if now - last < idle_min * 60:
                continue
            freed += sum(f.stat().st_size for f in files) / 1e6
        except OSError:
            continue
        n += 1
        if not dry:
            shutil.rmtree(j, ignore_errors=True)
    return n, round(freed)


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
    try:  # whole job folders nobody needs any more (their clips were removed from Ready to post)
        on, omb = remove_orphans(dry)
        n += on; freed += omb
    except Exception:
        pass
    return n, round(freed)


if __name__ == "__main__":
    dry = "--dry" in sys.argv
    age = int(sys.argv[sys.argv.index("--age") + 1]) if "--age" in sys.argv else 30  # minutes a job must be idle (testing: --age 0)
    n, mb = run(dry, age)
    print(f"{'would delete' if dry else 'deleted'} {n} file(s), {mb} MB; jobs folder now {jobs_mb()} MB")
