"""What does Clippy learn from your ratings? Three kinds of clips: 👍 stood out as good (+1), 👎 stood out as bad (-1), and clips with no rating = "meh" (0: not bad,
just not worth posting; neutral, it counts with a low weight). Everything is read from ratings.jsonl (written by the dashboard, local only) plus the clips still
waiting in review/ (the unrated ones are the meh examples). Nothing leaves this PC.

python scripts/learn.py        (report: the average of every signal for 👍, 👎 and meh clips)
python scripts/learn.py fit    (learn now: writes learned_weights.json)

How it learns (so it fits every user: gaming streams, podcasts, IRL, other games...): a small ridge regression on the signals saved with each clip (energy, voice, swearing,
laughter, motion, the AI's scores, clip length, Twitch/YouTube source, speech speed ...) plus a per-streamer / per-channel taste. It starts after a handful of ratings
and gets more trusted as more come in: the nudge is scaled by a confidence that grows with the number of 👍/👎 and drops to 0 when the ratings show no pattern
(leave-one-out check). The nudge is at most +-1.5 on a rank of about 5-10, so it steers, it never takes over."""
import datetime as dt, json, math, pathlib, statistics as stt

R = pathlib.Path(__file__).resolve().parent.parent
W_FILE = R / "learned_weights.json"
MIN_TOTAL, MIN_GOOD, MIN_REST, MIN_RATED = 12, 3, 3, 6  # clips in total; 👍; clips that were not 👍 (👎 or meh); clips rated 👍 or 👎 (the ratings are what teaches)
FULL_AT = 30  # rated clips at which the learning is fully trusted
MEH_CAP, MEH_WEIGHT = 2, 0.3  # at most 2 meh clips per rated one, and each counts about a third of a clip that stood out
MAX_NUDGE, MAX_CHANNEL = 1.5, 0.6
KEYS = ["win", "frames", "ai", "score", "penalty", "seconds", "twitch", "views_log", "wps", "sig.loud", "sig.hype", "sig.motion", "sig.voice", "sig.laugh", "sig.swear",
        "sig.novel", "sig.rate", "sig.chat", "sig.game"]
LABELS = {"win": "the AI's funny / watchable score", "frames": "what the frames show", "ai": "the AI window score", "score": "the energy of the moment", "penalty": "few quality penalties",
          "seconds": "longer clips", "twitch": "clips viewers made on Twitch", "views_log": "popular sources", "wps": "fast talking", "sig.loud": "loud moments",
          "sig.hype": "hype words", "sig.motion": "action on screen", "sig.voice": "an excited voice", "sig.laugh": "laughter", "sig.swear": "swearing",
          "sig.novel": "unusual / glitchy things", "sig.rate": "fast speech", "sig.chat": "chat reactions", "sig.game": "talk about the game"}


def flat(d, pre=""):
    for k, v in (d or {}).items():
        if isinstance(v, dict):
            yield from flat(v, pre + k + ".")
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            yield pre + k, float(v)


def channel_of(r):
    f = r.get("features") or {}
    return str(f.get("channel") or r.get("creator") or "").strip().lower()


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
                        "seconds": d.get("seconds"), "creator": d.get("source_channel") or d.get("creator")}
    return list(rows.values())


def report(rows):
    good, weak, meh = [r for r in rows if r["rating"] == 1], [r for r in rows if r["rating"] == -1], [r for r in rows if r["rating"] == 0]
    lines = [f"{len(rows)} clips: {len(good)} stood out as good, {len(weak)} stood out as bad, {len(meh)} meh (no rating)" + ("" if len(rows) >= 30 else "  (too few to trust: ~30 needed)")]
    feats = {}
    for r in rows:
        f = dict(flat(r.get("features")))
        if r.get("rank") is not None:
            f["rank"] = float(r["rank"])
        if r.get("seconds") and "seconds" not in f:
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


def vec(features):
    f = dict(flat(features))
    return [f.get(k) for k in KEYS]


def counts(rows):
    good = [r for r in rows if r["rating"] == 1]; weak = [r for r in rows if r["rating"] == -1]; meh = [r for r in rows if r["rating"] == 0]
    return good, weak, meh


def ready(good, weak, meh):
    return (len(good) + len(weak) + len(meh) >= MIN_TOTAL and len(good) >= MIN_GOOD and len(weak) + len(meh) >= MIN_REST and len(good) + len(weak) >= MIN_RATED)


def _ridge(Z, y, sw, lam):
    import numpy as np
    ybar = float((sw * y).sum() / sw.sum())
    w = np.linalg.solve(Z.T @ (Z * sw[:, None]) + lam * np.eye(Z.shape[1]), Z.T @ (sw * (y - ybar)))
    return w, ybar


def _wcorr(p, y, sw):
    import numpy as np
    sw = sw / sw.sum(); pm, ym = (sw * p).sum(), (sw * y).sum()
    cov = (sw * (p - pm) * (y - ym)).sum(); vp, vy = (sw * (p - pm) ** 2).sum(), (sw * (y - ym) ** 2).sum()
    return float(cov / math.sqrt(vp * vy)) if vp > 1e-12 and vy > 1e-12 else 0.0


def fit(rows=None):
    """Learn from the ratings. Returns the model dict (also saved to learned_weights.json), or None when there is too little to learn from yet."""
    import numpy as np, warnings
    rows = load_all() if rows is None else rows
    good, weak, meh = counts(rows)
    if not ready(good, weak, meh):
        return None
    meh = sorted(meh, key=lambda r: r["id"], reverse=True)[:MEH_CAP * (len(good) + len(weak))]  # the newest meh clips, never more than twice the rated ones
    rows = good + weak + meh
    X = np.array([[np.nan if x is None else x for x in vec(r.get("features"))] for r in rows], dtype=float)
    y = np.array([r["rating"] for r in rows], dtype=float)
    sw = np.where(y == 0, MEH_WEIGHT, 1.0)  # meh is neutral: the clips that stood out drive the learning
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # a signal that is missing in every clip is simply ignored
        mean = np.nanmean(X, axis=0)
    mean = np.where(np.isnan(mean), 0.0, mean)
    X = np.where(np.isnan(X), mean, X)
    std = X.std(axis=0); std = np.where(std < 1e-6, 1.0, std)
    Z = (X - mean) / std
    lam = 5.0  # strong regularisation: with few ratings the nudge must stay small and sensible
    w, ybar = _ridge(Z, y, sw, lam)
    # does it generalise? leave-one-out: predict every clip from a model that has not seen it, and check the predictions follow the ratings
    loo = np.zeros(len(rows))
    for i in range(len(rows)):
        keep = np.arange(len(rows)) != i
        wi, bi = _ridge(Z[keep], y[keep], sw[keep], lam)
        loo[i] = Z[i] @ wi
    corr = _wcorr(loo, y, sw)
    rated = len(good) + len(weak)
    conf = round(max(0.0, corr) * min(1.0, rated / FULL_AT) if corr > 0.1 else 0.0, 3)  # no visible pattern -> no nudge at all
    # per-streamer / per-channel taste (shrunk towards 0: one clip says little)
    agg = {}
    for r, s_ in zip(rows, sw):
        ch = channel_of(r)
        if ch:
            a = agg.setdefault(ch, [0.0, 0.0, 0]); a[0] += s_ * r["rating"]; a[1] += s_; a[2] += 1
    channels = {ch: round(a[0] / (a[1] + 3.0), 3) for ch, a in agg.items() if a[2] >= 2 and abs(a[0] / (a[1] + 3.0)) >= 0.05}
    model = {"keys": KEYS, "mean": mean.tolist(), "std": std.tolist(), "w": w.tolist(), "bias": ybar, "n": len(rows), "good": len(good), "weak": len(weak), "meh": len(meh),
             "rated": rated, "corr": round(corr, 3), "conf": conf, "channels": channels, "ts": dt.datetime.now().isoformat(timespec="seconds")}
    W_FILE.write_text(json.dumps(model), encoding="utf-8")
    return model


_model = {"t": 0.0, "m": None}


def model(force=False):
    """The saved model (re-read when the file changes)."""
    try:
        mt = W_FILE.stat().st_mtime if W_FILE.exists() else -1
    except OSError:
        mt = -1
    if force or _model["t"] != mt:
        _model["t"] = mt
        try:
            _model["m"] = json.loads(W_FILE.read_text(encoding="utf-8")) if mt >= 0 else None
        except Exception:
            _model["m"] = None
    return _model["m"]


def confidence(m=None):
    """0..1: how much Clippy trusts what it learned (0 = nothing learned yet / no pattern). The built-in rules (e.g. the voice / swearing boost) fade as this grows."""
    m = m if m is not None else model()
    return float((m or {}).get("conf", 0.0))


def adjust(features, m=None):
    """The learned rank nudge for one moment (-1.5..+1.5, 0 when nothing is learned yet): how much it looks like the clips the user liked or did not like, plus the
    taste for that streamer / channel."""
    m = m if m is not None else model()
    conf = confidence(m)
    if not m or conf <= 0:
        return 0.0
    v = vec(features)
    z = sum(((mu if x is None else x) - mu) / sd * w for x, mu, sd, w in zip(v, m["mean"], m["std"], m["w"]))
    ch = str((features or {}).get("channel") or "").strip().lower()
    aff = (m.get("channels") or {}).get(ch, 0.0)
    return round(conf * (MAX_NUDGE * math.tanh(z) + MAX_CHANNEL * aff), 2)


def top_features(m, k=5):
    pairs = sorted(zip(m["keys"], m["w"]), key=lambda t: -abs(t[1]))[:k]
    return ", ".join(f"{n} {'+' if w > 0 else '-'}{abs(w):.2f}" for n, w in pairs)


def summary(rows=None):
    """Everything the dashboard shows about what Clippy learned (plain words)."""
    rows = load_all() if rows is None else rows
    good, weak, meh = counts(rows)
    m = model()
    out = {"n": len(rows), "good": len(good), "weak": len(weak), "meh": len(meh), "rated": len(good) + len(weak),
           "need": {"rated": MIN_RATED, "good": MIN_GOOD, "total": MIN_TOTAL}, "ready": ready(good, weak, meh), "active": confidence(m) > 0, "conf": round(confidence(m), 2),
           "likes": [], "dislikes": [], "channels_like": [], "channels_dislike": []}
    if m and m.get("w"):
        for key, w in sorted(zip(m["keys"], m["w"]), key=lambda t: -abs(t[1])):
            if abs(w) < 0.03 or key not in LABELS:
                continue
            (out["likes"] if w > 0 else out["dislikes"]).append(LABELS[key])
        out["likes"], out["dislikes"] = out["likes"][:4], out["dislikes"][:4]
        for ch, a in sorted((m.get("channels") or {}).items(), key=lambda t: -abs(t[1])):
            (out["channels_like"] if a > 0 else out["channels_dislike"]).append(ch)
        out["channels_like"], out["channels_dislike"] = out["channels_like"][:4], out["channels_dislike"][:4]
        out["corr"], out["ts"] = m.get("corr"), m.get("ts")
    return out


def reset():
    """Start fresh: the learned model is deleted and the ratings log is set aside (renamed, not deleted)."""
    W_FILE.unlink(missing_ok=True)
    f = R / "ratings.jsonl"
    if f.exists():
        f.rename(R / f"ratings.before-reset-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.jsonl")
    for g in (R / "review").glob("*__clip_*.json"):  # clips still waiting keep their saved signals, but not their old rating
        if g.name.endswith(".posted.json"):
            continue
        try:
            d = json.loads(g.read_text(encoding="utf-8"))
            if d.get("rating"):
                d["rating"] = 0
                g.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception:
            pass
    model(force=True)


if __name__ == "__main__":
    import sys
    rows = load_all()
    if len(sys.argv) > 1 and sys.argv[1] == "fit":
        m = fit(rows)
        if m:
            print(f"learned from {m['n']} clips ({m['good']} 👍, {m['weak']} 👎, {m['meh']} meh). Trust {m['conf']:.0%} (leave-one-out correlation {m['corr']:+.2f}). Strongest signals: {top_features(m)}")
        else:
            print(f"Not learning yet ({len(rows)} clips; needs {MIN_TOTAL} clips with at least {MIN_GOOD} 👍 and {MIN_RATED} rated 👍 or 👎).")
    else:
        print(report(rows) if rows else "No clips with saved signals yet.")
