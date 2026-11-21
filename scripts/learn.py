"""What do your 👍/👎 ratings say? Three kinds of clips: 👍 stood out as good (+1), 👎 stood out as bad (-1), and clips with no rating = "meh" (0: not
bad, just not worth posting). Reads ratings.jsonl (written by the dashboard, local only) plus the clips still waiting in review/ (the unrated ones are the "meh"
examples) and prints, for every signal Clippy logs with a clip, the average per kind. Needs ~30 clips to mean anything. The report changes nothing.
python scripts/learn.py        (report)
python scripts/learn.py fit    (learn: writes learned_weights.json, only with 30+ clips, 8+ 👍 and 12+ clips rated 👍 or 👎; meh clips count little)
Meh clips are neutral (counted with a low weight). Once learned_weights.json exists, Clippy adds a small nudge (at most +-1 on a rank of about 5-10) to moments that look like your 👍 clips and subtracts for moments that look like your 👎 ones."""
import datetime as dt, json, math, pathlib, statistics as stt

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
    return [r for r in last.values() if r.get("rating") in (1, -1, 0) and r.get("features")]  # 0 = meh (also a removed clip nobody rated)


def load_all():
    """Every clip Clippy can learn from: the rated ones (kept in ratings.jsonl even after the clip was removed) plus the clips still in review/ that have signals
    saved: with no rating they count as 'meh' (rating 0)."""
    rows = {r["id"]: r for r in load()}
    for f in (R / "review").glob("*__clip_*.json"):
        if f.name.endswith(".posted.json") or f.stem in rows:
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not d.get("features"):
            continue  # clips from before the signals were saved cannot teach anything
        rows[f.stem] = {"id": f.stem, "rating": d.get("rating") if d.get("rating") in (1, -1) else 0, "rank": d.get("rank"), "features": d.get("features"),
                        "seconds": d.get("seconds"), "creator": d.get("creator")}
    return list(rows.values())


def report(rows):
    good, weak, meh = [r for r in rows if r["rating"] == 1], [r for r in rows if r["rating"] == -1], [r for r in rows if r["rating"] == 0]
    lines = [f"{len(rows)} clips: {len(good)} stood out as good, {len(weak)} stood out as bad, {len(meh)} meh (no rating)" + ("" if len(rows) >= 30 else "  (too few to trust: ~30 needed)")]
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
        m = [feats[r["id"]][k] for r in meh if k in feats[r["id"]]]
        if g and (w or m):
            lines.append(f"  {k:<18} good {stt.mean(g):6.2f}   bad {stt.mean(w) if w else float('nan'):6.2f}   meh {stt.mean(m) if m else float('nan'):6.2f}   good vs rest {stt.mean(g) - stt.mean(w + m):+.2f}")
    return "\n".join(lines)


W_FILE = R / "learned_weights.json"
MIN_RATED, MIN_EACH, MIN_STOOD = 30, 8, 12  # clips in total; at least 8 👍; and at least 12 clips with a 👍 or 👎 (the ratings are what teaches, not the meh ones)
MEH_CAP = 2  # at most this many meh clips per rated clip: meh can never outnumber what the user actually rated by much
MEH_WEIGHT = 0.3  # a meh clip is neutral: it counts as an example, but only about a third as much as a clip that stood out (good or bad)
KEYS = ["win", "frames", "ai", "score", "penalty", "sig.loud", "sig.hype", "sig.motion", "sig.voice", "sig.laugh", "sig.swear", "sig.novel", "sig.rate", "sig.chat", "sig.game"]


def vec(features, rank=None):
    f = dict(flat(features))
    return [f.get(k) for k in KEYS]


def fit(rows=None):
    """Ridge regression of the rating (+1 / -1) on the clip's signals, standardised. Returns the model dict, or None when there is too little to learn from."""
    import numpy as np
    rows = load_all() if rows is None else rows
    good = [r for r in rows if r["rating"] == 1]; weak = [r for r in rows if r["rating"] == -1]; meh = [r for r in rows if r["rating"] == 0]
    if len(rows) < MIN_RATED or len(good) < MIN_EACH or len(good) + len(weak) < MIN_STOOD:
        return None
    meh = sorted(meh, key=lambda r: r["id"], reverse=True)[:MEH_CAP * (len(good) + len(weak))]  # the newest meh clips, never more than twice the rated ones
    rows = good + weak + meh
    X = np.array([[np.nan if x is None else x for x in vec(r.get("features"))] for r in rows], dtype=float)
    y = np.array([r["rating"] for r in rows], dtype=float)
    sw = np.where(y == 0, MEH_WEIGHT, 1.0)  # sample weights: meh is neutral, the clips that stood out drive the learning
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # a signal that is missing in every clip is simply ignored
        mean = np.nanmean(X, axis=0)
    mean = np.where(np.isnan(mean), 0.0, mean)
    X = np.where(np.isnan(X), mean, X)
    std = X.std(axis=0); std = np.where(std < 1e-6, 1.0, std)
    Z = (X - mean) / std
    lam = 5.0  # strong regularisation: with few ratings the nudge must stay small and sensible
    ybar = float((sw * y).sum() / sw.sum())
    w = np.linalg.solve(Z.T @ (Z * sw[:, None]) + lam * np.eye(Z.shape[1]), Z.T @ (sw * (y - ybar)))
    model = {"keys": KEYS, "mean": mean.tolist(), "std": std.tolist(), "w": w.tolist(), "bias": ybar, "n": len(rows), "good": len(good), "weak": len(weak), "meh": len(meh),
             "ts": dt.datetime.now().isoformat(timespec="seconds")}
    W_FILE.write_text(json.dumps(model), encoding="utf-8")
    return model


_model = {"t": 0, "m": None}


def adjust(features, model=None):
    """The learned rank nudge for one moment, -1..+1 (0 when nothing is learned yet)."""
    if model is None:
        if _model["m"] is None and _model["t"] == 0:
            _model["t"] = 1
            try:
                _model["m"] = json.loads(W_FILE.read_text(encoding="utf-8")) if W_FILE.exists() else None
            except Exception:
                _model["m"] = None
        model = _model["m"]
    if not model:
        return 0.0
    v = vec(features)
    z = sum(((m if x is None else x) - m) / sd * w for x, m, sd, w in zip(v, model["mean"], model["std"], model["w"]))
    return round(math.tanh(z), 2)  # squashed to -1..+1


def top_features(model, k=5):
    pairs = sorted(zip(model["keys"], model["w"]), key=lambda t: -abs(t[1]))[:k]
    return ", ".join(f"{n} {'+' if w > 0 else '-'}{abs(w):.2f}" for n, w in pairs)


if __name__ == "__main__":
    import sys
    rows = load_all()
    if len(sys.argv) > 1 and sys.argv[1] == "fit":
        m = fit(rows)
        print(f"learned from {m['n']} clips ({m['good']} good, {m['weak']} bad, {m['meh']} meh). Strongest signals: {top_features(m)}" if m else f"Not enough yet ({len(rows)} clips; need {MIN_RATED}, with at least {MIN_EACH} 👍 and {MIN_STOOD} clips rated 👍 or 👎).")
    else:
        print(report(rows) if rows else "No clips with saved signals yet.")
