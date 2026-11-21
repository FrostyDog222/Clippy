"""How did a posted clip do? About 2 days after a clip went to YouTube, read its public view count (yt-dlp, a normal YouTube page read; nothing is sent anywhere) and keep it
in views.jsonl (local only). The learner (learn.py) uses it as a weaker second teacher next to your 👍 / 👎: well above your usual views = a hit, far below = a miss.
Private videos have no public count and are simply skipped.   python scripts/views.py   (the dashboard also runs it about once an hour)"""
import datetime as dt, json, sys, time
from pathlib import Path

R = Path(__file__).resolve().parent.parent
REVIEW, LOG = R / "review", R / "views.jsonl"
WAIT_H = 48  # hours after posting before the count means something


def _read():
    last = {}
    for ln in (LOG.read_text(encoding="utf-8").splitlines() if LOG.exists() else []):
        try:
            r = json.loads(ln); last[r["id"]] = r
        except Exception:
            pass
    return last


def load():
    return list(_read().values())


def due(now=None):
    """Posted-to-YouTube clips older than WAIT_H hours, not read in the last 20 h (a later read replaces an earlier one until the clip is 10 days old)."""
    now, out, seen = now or time.time(), [], _read()
    for pf in REVIEW.glob("*__clip_*.posted.json"):
        stem = pf.name[: -len(".posted.json")]
        try:
            url = (json.loads(pf.read_text(encoding="utf-8")) or {}).get("youtube")
        except Exception:
            continue
        age_h = (now - pf.stat().st_mtime) / 3600
        if not (isinstance(url, str) and url.startswith("http")) or age_h < WAIT_H or age_h > 240:
            continue
        s = seen.get(stem)
        if s and now - s.get("t", 0) < 20 * 3600:
            continue
        out.append((stem, url))
    return out


def view_count(url):
    from yt_dlp import YoutubeDL
    with YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True, "socket_timeout": 20}) as y:
        n = (y.extract_info(url, download=False) or {}).get("view_count")
    return int(n) if isinstance(n, (int, float)) else None


def refresh(fetch=view_count, now=None):
    """Read the view counts that are due. Returns how many were stored."""
    done = 0
    for stem, url in due(now):
        try:
            n = fetch(url)
        except Exception:
            continue
        if n is None:
            continue
        try:
            d = json.loads((REVIEW / (stem + ".json")).read_text(encoding="utf-8"))
        except Exception:
            d = {}
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"id": stem, "views": n, "t": now or time.time(), "ts": dt.datetime.now().isoformat(timespec="seconds"), "rank": d.get("rank"), "features": d.get("features"),
                                 "seconds": d.get("seconds"), "creator": d.get("source_channel") or d.get("creator")}, ensure_ascii=False) + chr(10))
        done += 1
    return done


def derived(rows):
    """{id: (rating, weight)} from the view counts: needs 4 clips with views; >= 1.5x the median = hit (+1), <= 0.4x = miss (-1), otherwise meh (0)."""
    vs = sorted(r["views"] for r in rows if r.get("features"))
    if len(vs) < 4:
        return {}
    med = vs[len(vs) // 2] if len(vs) % 2 else (vs[len(vs) // 2 - 1] + vs[len(vs) // 2]) / 2
    if med <= 0:
        return {}
    return {r["id"]: ((1 if r["views"] >= 1.5 * med else -1 if r["views"] <= 0.4 * med else 0), 0.5) for r in rows if r.get("features")}


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    print(f"{refresh()} view counts stored")
