"""What do your 👍/👎 ratings say? Reads ratings.jsonl (written by the dashboard, local only) and prints, for every signal Clippy logs with a clip,
the average for good clips vs weak clips. Needs ~30 rated clips to mean anything. Changes nothing: it is a report to decide which weights to tune.
python scripts/learn.py"""
import json, pathlib, statistics as stt

R = pathlib.Path(__file__).resolve().parent.parent


def flat(d, pre=""):
    for k, v in (d or {}).items():
        if isinstance(v, dict):
            yield from flat(v, pre + k + ".")
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            yield pre + k, float(v)


def load():
    last = {}
    f = R / "ratings.jsonl"
    for ln in (f.read_text(encoding="utf-8").splitlines() if f.exists() else []):
        try:
            r = json.loads(ln)
            last[r["id"]] = r  # the newest rating of a clip wins
        except Exception:
            pass
    return [r for r in last.values() if r.get("rating") in (1, -1)]


def report(rows):
    good, weak = [r for r in rows if r["rating"] == 1], [r for r in rows if r["rating"] == -1]
    lines = [f"{len(rows)} rated clips: {len(good)} good, {len(weak)} weak" + ("" if len(rows) >= 30 else "  (too few to trust: rate ~30 clips)")]
    feats = {}
    for r in rows:
        f = dict(flat(r.get("features")))
        if r.get("rank") is not None:
            f["rank"] = float(r["rank"])
        if r.get("seconds"):
            f["seconds"] = float(r["seconds"])
        feats[r["id"]] = f
    keys = sorted({k for f in feats.values() for k in f})
    for k in keys:
        g = [feats[r["id"]][k] for r in good if k in feats[r["id"]]]
        w = [feats[r["id"]][k] for r in weak if k in feats[r["id"]]]
        if g and w:
            lines.append(f"  {k:<18} good {stt.mean(g):6.2f}   weak {stt.mean(w):6.2f}   gap {stt.mean(g) - stt.mean(w):+.2f}")
    return "\n".join(lines)


if __name__ == "__main__":
    rows = load()
    print(report(rows) if rows else "No ratings yet: press 👍 or 👎 under a clip in Ready to post.")
