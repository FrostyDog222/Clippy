"""What do your 👍/👎 ratings say? Reads ratings.jsonl (written by the dashboard, local only) and prints, for every signal Clippy logs with a clip,
the average for good clips vs weak clips. Needs ~30 rated clips to mean anything. Changes nothing: it is a report to decide which weights to tune.
python scripts/learn.py        (report)
python scripts/learn.py fit    (learn from the ratings: writes learned_weights.json, only with 30+ ratings and 8+ of each kind)
Once learned_weights.json exists, Clippy adds a small nudge (at most +-1 on a rank of about 5-10) to moments that look like your 👍 clips and subtracts for moments that look like your 👎 ones."""
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


W_FILE = R / "learned_weights.json"
MIN_RATED, MIN_EACH = 30, 8
KEYS = ["win", "frames", "ai", "score", "penalty", "sig.loud", "sig.hype", "sig.motion", "sig.voice", "sig.laugh", "sig.swear", "sig.novel", "sig.rate", "sig.chat", "sig.game"]


def vec(features, rank=None):
    f = dict(flat(features))
    return [f.get(k) for k in KEYS]


def fit(rows=None):
    """Ridge regression of the rating (+1 / -1) on the clip's signals, standardised. Returns the model dict, or None when there is too little to learn from."""
    import numpy as np
    rows = load() if rows is None else rows
    good = [r for r in rows if r["rating"] == 1]; weak = [r for r in rows if r["rating"] == -1]
    if len(rows) < MIN_RATED or len(good) < MIN_EACH or len(weak) < MIN_EACH:
        return None
    X = np.array([[np.nan if x is None else x for x in vec(r.get("features"))] for r in rows], dtype=float)
    y = np.array([r["rating"] for r in rows], dtype=float)
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # a signal that is missing in every clip is simply ignored
        mean = np.nanmean(X, axis=0)
    mean = np.where(np.isnan(mean), 0.0, mean)
    X = np.where(np.isnan(X), mean, X)
    std = X.std(axis=0); std = np.where(std < 1e-6, 1.0, std)
    Z = (X - mean) / std
    lam = 5.0  # strong regularisation: with few ratings the nudge must stay small and sensible
    w = np.linalg.solve(Z.T @ Z + lam * np.eye(Z.shape[1]), Z.T @ (y - y.mean()))
    model = {"keys": KEYS, "mean": mean.tolist(), "std": std.tolist(), "w": w.tolist(), "bias": float(y.mean()), "n": len(rows), "good": len(good), "weak": len(weak),
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
    rows = load()
    if len(sys.argv) > 1 and sys.argv[1] == "fit":
        m = fit(rows)
        print(f"learned from {m['n']} ratings ({m['good']} good, {m['weak']} weak). Strongest signals: {top_features(m)}" if m else f"Not enough ratings yet ({len(rows)} rated, need {MIN_RATED} with at least {MIN_EACH} good and {MIN_EACH} weak).")
    else:
        print(report(rows) if rows else "No ratings yet: press 👍 or 👎 under a clip in Ready to post.")
