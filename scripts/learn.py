"""What does Clippy learn from your ratings? Three kinds of clips: 👍 stood out as good (+1), 👎 stood out as bad (-1), and clips with no rating = "meh" (0: not bad,
just not worth posting; neutral, it counts with a low weight). Everything is read from ratings.jsonl (written by the dashboard, local only) plus the clips still
waiting in review/ (the unrated ones are the meh examples). Nothing leaves this PC.

python scripts/learn.py        (report: the average of every signal for 👍, 👎 and meh clips)
python scripts/learn.py fit    (learn now: writes learned_weights.json)

How it learns (so it fits every user: gaming streams, podcasts, IRL, other games...): a small ridge regression on the signals saved with each clip (energy, voice, swearing,
laughter, motion, the AI's scores, clip length, Twitch/YouTube source, speech speed ...) plus a per-streamer / per-channel taste. It starts after a handful of ratings
and gets more trusted as more come in: the nudge is scaled by a confidence that grows with the number of 👍/👎 and drops to 0 when the ratings show no pattern
(leave-one-out check). The nudge is at most +-1.5 on a rank of about 5-10, so it steers, it never takes over."""
import datetime as dt, json, math, pathlib, statistics as stt, time

R = pathlib.Path(__file__).resolve().parent.parent
W_FILE = R / "learned_weights.json"
MIN_TOTAL, MIN_GOOD, MIN_REST, MIN_RATED = 12, 3, 3, 6  # clips in total; 👍; clips that were not 👍 (👎 or meh); clips rated 👍 or 👎 (the ratings are what teaches)
FULL_AT = 30  # rated clips at which the learning is fully trusted
MEH_CAP, MEH_WEIGHT = 2, 0.3  # at most 2 meh clips per rated one, and each counts about a third of a clip that stood out
MAX_NUDGE, MAX_CHANNEL, MAX_KIND = 1.5, 0.6, 0.4
POSTED_WEIGHT = 0.5  # a clip you posted without rating it counts as half a 👍
# polish 4 (2026-11-22): the old 21 signals were too many for ~50 ratings (cross-validated AUC 0.33 = worse than guessing). These four held up: the AI's read of the bit, what the frames
# show, whether the streamer is on the user's list, and a 20-30 s clip (cross-validated AUC about 0.65, shuffled-ratings p about 0.1 on the first 47 ratings).
KEYS = ["win", "frames", "in_list", "len2030"]
OLD_KEYS = ["win", "frames", "ai", "score", "penalty", "seconds", "twitch", "live", "views_log", "wps", "sig.loud", "sig.hype", "sig.motion", "sig.voice", "sig.laugh", "sig.swear",
           "sig.novel", "sig.rate", "sig.chat", "sig.game", "nonen"]
LABELS = {"in_list": "streamers on your list", "len2030": "clips of 20-30 seconds", "win": "the AI's funny / watchable score", "frames": "what the frames show", "ai": "the AI window score", "score": "the energy of the moment", "penalty": "few quality penalties",
          "seconds": "longer clips", "twitch": "clips viewers made on Twitch", "live": "clips from live streams", "views_log": "popular sources", "wps": "fast talking", "sig.loud": "loud moments",
          "sig.hype": "hype words", "sig.motion": "action on screen", "sig.voice": "an excited voice", "sig.laugh": "laughter", "sig.swear": "swearing",
          "sig.novel": "unusual / glitchy things", "sig.rate": "fast speech", "sig.chat": "chat reactions", "sig.game": "talk about the game", "nonen": "speech that is not English"}


def flat(d, pre=""):
    for k, v in (d or {}).items():
        if isinstance(v, dict):
            yield from flat(v, pre + k + ".")
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            yield pre + k, float(v)


OFF_MOMENT = ("nudity", "crop", "repeat", "audio", "captions", "cutoff", "title", "language")  # reasons about the making, not about the moment

# Words in the user's own reasons that tell Clippy what they mean (any language of the user: English words here, plus the clip's own reason text is kept as typed).
REASON_WORDS = {"crop": ("crop", "framing", "zoom", "cut off", "cropped", "face", "webcam", "layout", "black bars"), "audio": ("sound", "audio", "volume", "loud", "music", "noise", "mic", "quiet"),
                "captions": ("caption", "subtitle", "text on screen", "font"), "title": ("title", "headline", "joke", "description", "hashtag"), "cutoff": ("too early", "too late", "ends", "starts", "cut short", "abrupt"),
                "language": ("language", "foreign", "translated", "english"), "nudity": ("nude", "naked", "nsfw", "sexual"), "repeat": ("repeat", "again", "already posted", "same clip", "duplicate"),
                "boring": ("boring", "dull", "bland", "meh"), "slow": ("slow", "long", "drag", "waiting", "nothing happening"), "notfunny": ("not funny", "unfunny", "cringe"),
                "nocontext": ("context", "confusing", "no idea", "doesn't make sense", "unclear"), "offtopic": ("off topic", "not gta", "unrelated", "other game", "irrelevant")}


def canon(r):
    """A reason the user typed -> the known reason it means (or the text itself when nothing matches)."""
    t = str(r).strip().lower()
    if t in REASON_WORDS or not t:
        return t
    for k, ws in REASON_WORDS.items():
        if any(w in t for w in ws):
            return k
    return t


def off_moment(r):
    rs = [canon(x) for x in (r.get("reasons") or ([r["reason"]] if r.get("reason") else []))]
    return bool(rs) and all(x in OFF_MOMENT for x in rs)



def channel_of(r):
    f = r.get("features") or {}
    try:
        import pick_rules
        return pick_rules.norm_channel(f.get("channel") or r.get("creator") or "")  # polish 2: one key per streamer, however it is spelled
    except Exception:
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
        if f.name.endswith(".posted.json"):
            continue
        if f.stem in rows:  # the log has this clip: older log lines kept only the first reason, the clip itself has them all
            if not rows[f.stem].get("reasons"):
                try:
                    rs = json.loads(f.read_text(encoding="utf-8")).get("reasons")
                    if rs and rows[f.stem].get("rating") == -1:
                        rows[f.stem] = dict(rows[f.stem], reasons=rs)
                except Exception:
                    pass
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not d.get("features"):
            continue  # clips from before the signals were saved cannot teach anything
        try:  # polish 1: a clip that is still waiting for you (not rated, not marked 😐, made in the last 2 days) has not been judged yet: it is not a "meh"
            if d.get("rating") not in (1, -1) and not d.get("reviewed") and time.time() - int(f.stem.split("__")[0]) < 2 * 86400:
                continue
        except Exception:
            pass
        rt, wt = (d.get("rating") if d.get("rating") in (1, -1) else 0), 1.0
        if rt == 0:  # polish 7: a clip you posted (and did not rate) is a soft like: you chose to publish it
            try:
                pf = f.with_name(f.stem + ".posted.json")
                if pf.exists() and any(json.loads(pf.read_text(encoding="utf-8")).values()):
                    rt, wt = 1, POSTED_WEIGHT
            except Exception:
                pass
        rows[f.stem] = {"id": f.stem, "rating": rt, "w": wt, "rank": d.get("rank"), "features": d.get("features"),
                        "seconds": d.get("seconds"), "creator": d.get("source_channel") or d.get("creator"), "reason": d.get("reason") if d.get("rating") == -1 else None, "reasons": d.get("reasons") if d.get("rating") == -1 else None}
    try:  # how the posted clips did (views.py): a weaker second teacher, only where the user did not rate the clip
        import views
        vr = views.load(); byid = {r["id"]: r for r in vr}
        for i, (rt, w) in views.derived(vr).items():
            if i not in rows:
                v = byid[i]; rows[i] = {"id": i, "rating": rt, "w": w, "rank": v.get("rank"), "features": v.get("features"), "seconds": v.get("seconds"), "creator": v.get("creator")}
            elif rows[i]["rating"] == 0 or rows[i].get("w") == POSTED_WEIGHT:  # the real views of a posted clip outrank the soft like of having posted it
                rows[i] = dict(rows[i], rating=rt, w=w)
    except Exception:
        pass
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


_listed = {"t": None, "v": set()}


def listed():
    """The streamers on the user's list (normalised), re-read when the settings file changes."""
    try:
        import pick_rules
        p = R / "autopost_config.json"
        mt = p.stat().st_mtime if p.exists() else None
        if mt != _listed["t"]:
            _listed["t"] = mt
            _listed["v"] = {pick_rules.norm_channel(c) for c in (json.loads(p.read_text(encoding="utf-8-sig")).get("creators") or [])} if p.exists() else set()
    except Exception:
        pass
    return _listed["v"]


def vec(features):
    f = dict(flat(features))
    sec = f.get("seconds")
    f["len2030"] = (1.0 if 20 <= float(sec) <= 30 else 0.0) if sec else None  # the length band the ratings favoured
    ch = (features or {}).get("channel")
    try:
        import pick_rules
        f["in_list"] = (1.0 if pick_rules.norm_channel(ch) in listed() else 0.0) if ch else None
    except Exception:
        f["in_list"] = None
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


def examples(k=3):
    """The viewer's own taste for the AI judge (Settings: your rated examples): up to k liked and k disliked clips, newest first, as one short line each. Only the AI's own short description
    of the bit (and the quoted punchline for liked clips, the viewer's reasons for disliked ones) leaves the PC, never a transcript."""
    good, weak, _ = counts(load_all())
    def line(r, liked):
        f = r.get("features") or {}
        bit = str(f.get("bit") or "").strip()
        if not bit:
            return None
        kind = f" ({f['kind']})" if f.get("kind") else ""
        if liked:
            key = f.get("quote") or f.get("funny")
            return "LIKED" + kind + ": " + bit + (f' | the key line was: "{str(key)[:80]}"' if key else (f' | what made it: {str(f.get("what") or f.get("interesting"))[:80]}' if (f.get("what") or f.get("interesting")) else ""))
        rs = r.get("reasons") or ([r["reason"]] if r.get("reason") else [])
        return "DISLIKED" + kind + ": " + bit + (" | the viewer said: " + ", ".join(str(x) for x in rs[:3]) if rs else "")
    out = []
    for rows, liked in ((sorted(good, key=lambda r: r["id"], reverse=True), True), (sorted(weak, key=lambda r: r["id"], reverse=True), False)):
        out += [x for x in (line(r, liked) for r in rows) if x][:k]
    return out


def _ft(r):
    """The clip's saved signals plus the streamer and length when only the row has them (older rows)."""
    f = dict(r.get("features") or {})
    if not f.get("channel") and r.get("creator"):
        f["channel"] = r["creator"]
    if not f.get("seconds") and r.get("seconds"):
        f["seconds"] = r["seconds"]
    return f


def _auc(p, y):
    import numpy as np
    pos, neg = p[y == 1], p[y == -1]
    if not len(pos) or not len(neg):
        return 0.5
    return float(np.mean([(a > b) + 0.5 * (a == b) for a in pos for b in neg]))


def _cv_auc(Z, y, sw, lam, reps=20, k=5, seed=0):
    """Repeated k-fold cross-validation: every clip is predicted by models that never saw it. AUC among the 👍 / 👎 clips (0.5 = guessing)."""
    import numpy as np
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(reps):
        idx = rng.permutation(len(y)); pred = np.zeros(len(y))
        for fold in np.array_split(idx, k):
            tr = np.setdiff1d(idx, fold)
            w, _b = _ridge(Z[tr], y[tr], sw[tr], lam)
            pred[fold] = Z[fold] @ w
        out.append(_auc(pred, y))
    return float(np.mean(out))


def _auc_lower(auc, n_pos, n_neg, z=1.96, inflate=1.5):
    """Lower end of a confidence interval for an AUC (Hanley-McNeil standard error). Cross-validated scores are noisier than that formula says (Varoquaux 2018), so the error is widened by `inflate`."""
    if n_pos < 2 or n_neg < 2:
        return 0.0
    a = min(max(auc, 0.5), 0.999)
    q1, q2 = a / (2 - a), 2 * a * a / (1 + a)
    var = (a * (1 - a) + (n_pos - 1) * (q1 - a * a) + (n_neg - 1) * (q2 - a * a)) / (n_pos * n_neg)
    return a - z * inflate * math.sqrt(max(var, 0.0))


def _perm_p(Z, y, sw, lam, real, n=60):
    """How often shuffled ratings (no pattern at all) score as well as the real ones: the smaller, the more real the pattern."""
    import numpy as np
    rng = np.random.default_rng(1)
    hits = sum(1 for _ in range(n) if _cv_auc(Z, rng.permutation(y), sw, lam, reps=3, seed=int(rng.integers(1_000_000))) >= real)
    return (hits + 1) / (n + 1)


def fit(rows=None):
    """Learn from the ratings. Returns the model dict (also saved to learned_weights.json), or None when there is too little to learn from yet."""
    import numpy as np, warnings
    rows = load_all() if rows is None else rows
    good, weak, meh = counts(rows)
    if not ready(good, weak, meh):
        return None
    meh = sorted(meh, key=lambda r: r["id"], reverse=True)[:MEH_CAP * (len(good) + len(weak))]  # the newest meh clips, never more than twice the rated ones
    rows = good + weak + meh
    X = np.array([[np.nan if x is None else x for x in vec(_ft(r))] for r in rows], dtype=float)
    y = np.array([r["rating"] for r in rows], dtype=float)
    sw = np.where(y == 0, MEH_WEIGHT, 1.0)  # meh is neutral: the clips that stood out drive the learning
    sw = sw * np.array([r.get("w", 1.0) for r in rows])
    sw = np.where([off_moment(r) for r in rows], 0.35, sw)  # a 👎 for nudity / crop / a repeated joke says little about the MOMENT itself
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # a signal that is missing in every clip is simply ignored
        mean = np.nanmean(X, axis=0)
    mean = np.where(np.isnan(mean), 0.0, mean)
    X = np.where(np.isnan(X), mean, X)
    std = X.std(axis=0); std = np.where(std < 1e-6, 1.0, std)
    Z = (X - mean) / std
    lam = 5.0  # strong regularisation: with few ratings the nudge must stay small and sensible
    w, ybar = _ridge(Z, y, sw, lam)
    # does it generalise? polish 4: repeated cross-validation (leave-one-out was biased towards negative scores even for random signals) and a shuffled-ratings test
    rated = len(good) + len(weak)
    auc = _cv_auc(Z, y, sw, lam)
    p = _perm_p(Z, y, sw, lam, auc)
    corr = round(2 * (auc - 0.5), 3)  # 0 = guessing, 1 = perfect (kept under the old name for the dashboard)
    low = _auc_lower(auc, len(good), len(weak))
    # polish 4b (from the research): switch on only when the shuffled-ratings test passes (p <= 0.05) AND the lower end of the AUC interval is above guessing; trust then grows with that margin
    conf = round(min(1.0, max(0.0, (low - 0.5) / 0.1)) * min(1.0, rated / FULL_AT), 3) if p <= 0.05 else 0.0
    # per-streamer / per-channel taste (shrunk towards 0: one clip says little)
    agg = {}
    for r, s_ in zip(rows, sw):
        ch = channel_of(r)
        if ch:
            a = agg.setdefault(ch, [0.0, 0.0, 0]); a[0] += s_ * r["rating"]; a[1] += s_; a[2] += 1
    channels = {ch: round(a[0] / (a[1] + 3.0), 3) for ch, a in agg.items() if a[2] >= 2 and abs(a[0] / (a[1] + 3.0)) >= 0.05}
    kagg = {}  # polish 7: the same shrunk average per kind of moment (funny, drama, skill...): which kinds this user likes
    for r, s_ in zip(rows, sw):
        k = str((r.get("features") or {}).get("kind") or "")
        if k and k != "neither":
            a = kagg.setdefault(k, [0.0, 0.0, 0]); a[0] += s_ * r["rating"]; a[1] += s_; a[2] += 1
    kinds = {k: round(a[0] / (a[1] + 3.0), 3) for k, a in kagg.items() if a[2] >= 2 and abs(a[0] / (a[1] + 3.0)) >= 0.05}
    model = {"keys": KEYS, "mean": mean.tolist(), "std": std.tolist(), "w": w.tolist(), "bias": ybar, "n": len(rows), "good": len(good), "weak": len(weak), "meh": len(meh),
             "rated": rated, "corr": round(corr, 3), "auc": round(auc, 3), "auc_low": round(low, 3), "p": round(p, 3), "conf": conf, "channels": channels, "kinds": kinds, "ts": dt.datetime.now().isoformat(timespec="seconds")}
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
    if m and m.get("keys") != KEYS:  # a model saved with another set of signals is not trusted until the next fit
        return 0.0
    return float((m or {}).get("conf", 0.0))


def adjust(features, m=None):
    """The learned rank nudge for one moment (-1.5..+1.5, 0 when nothing is learned yet): how much it looks like the clips the user liked or did not like, plus the
    taste for that streamer / channel."""
    m = m if m is not None else model()
    conf = confidence(m)
    if m and m.get("keys") != KEYS:  # a model saved with another set of signals: not used until the next fit
        conf = 0.0
    if not m:
        return 0.0
    taste = MAX_CHANNEL * (m.get("channels") or {}).get(channel_of({"features": features}), 0.0) + MAX_KIND * (m.get("kinds") or {}).get(str((features or {}).get("kind") or ""), 0.0)
    taste *= min(1.0, float(m.get("rated", 0)) / FULL_AT)
    if conf <= 0:  # no clear pattern in the signals: the taste for a streamer and for a kind of moment still counts, a little (the sturdier part)
        return round(taste, 2)
    v = vec(features)
    z = sum(((mu if x is None else x) - mu) / sd * w for x, mu, sd, w in zip(v, m["mean"], m["std"], m["w"]))
    return round(conf * MAX_NUDGE * math.tanh(z) + taste, 2)


def top_features(m, k=5):
    pairs = sorted(zip(m["keys"], m["w"]), key=lambda t: -abs(t[1]))[:k]
    return ", ".join(f"{n} {'+' if w > 0 else '-'}{abs(w):.2f}" for n, w in pairs)


def reason_counts(rows):
    """How often each reason was ticked on a 👎, most common first: [[reason, count]]."""
    import collections
    c = collections.Counter()
    for r in rows:
        if r.get("rating") == -1:
            for x in (r.get("reasons") or ([r["reason"]] if r.get("reason") else [])):
                c[str(x)] += 1
    return [[k, n] for k, n in c.most_common(8)]


def rates(rows):
    """Like-rate per group with a Wilson range, from the clips rated up or down: [{name, k, n, lo, hi}]. Groups with fewer than 3 rated clips are left out."""
    import pick_rules
    rated = [r for r in rows if r["rating"] in (1, -1)]
    mine = listed()
    groups = [
        ("Streamers on your list", lambda r: channel_of(r) in mine),  # channel_of already normalises (normalising twice can change a name)
        ("Other streamers", lambda r: bool(channel_of(r)) and channel_of(r) not in mine),
        ("Twitch clips", lambda r: (r.get("features") or {}).get("twitch") == 1),
        ("Videos and VODs", lambda r: (r.get("features") or {}).get("twitch") != 1 and (r.get("features") or {}).get("live") != 1),
        ("Live streams", lambda r: (r.get("features") or {}).get("live") == 1),
        ("Clips of 20-30 s", lambda r: bool(r.get("seconds") or (r.get("features") or {}).get("seconds")) and 20 <= float(r.get("seconds") or (r.get("features") or {}).get("seconds")) <= 30),
        ("Other lengths", lambda r: bool(r.get("seconds") or (r.get("features") or {}).get("seconds")) and not 20 <= float(r.get("seconds") or (r.get("features") or {}).get("seconds")) <= 30),
    ]
    for k in ("funny", "drama", "shock", "skill", "fail", "emotional", "interesting"):  # how often each kind of moment was liked (appears once there are 3 rated clips of that kind)
        groups.append((k.capitalize() + " moments", lambda r, k=k: (r.get("features") or {}).get("kind") == k))
    out = []
    for name, fn in groups:
        xs = [r for r in rated if fn(r)]
        if len(xs) >= 3:
            k = sum(1 for r in xs if r["rating"] == 1)
            lo, hi = pick_rules.wilson(k, len(xs))
            out.append({"name": name, "k": k, "n": len(xs), "lo": round(lo, 2), "hi": round(hi, 2)})
    return out


def summary(rows=None):
    """Everything the dashboard shows about what Clippy learned (plain words)."""
    rows = load_all() if rows is None else rows
    good, weak, meh = counts(rows)
    m = model()
    out = {"n": len(rows), "good": len(good), "weak": len(weak), "meh": len(meh), "rated": len(good) + len(weak),
           "need": {"rated": MIN_RATED, "good": MIN_GOOD, "total": MIN_TOTAL}, "ready": ready(good, weak, meh), "active": confidence(m) > 0 and (m or {}).get("keys") == KEYS, "conf": round(confidence(m), 2), "taste_only": bool((m or {}).get("channels")),
           "likes": [], "dislikes": [], "channels_like": [], "channels_dislike": [], "rates": rates(rows), "reasons": reason_counts(rows), "kinds_like": [], "kinds_dislike": []}
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
        for k, a in sorted((m.get("kinds") or {}).items(), key=lambda t: -abs(t[1])):
            (out["kinds_like"] if a > 0 else out["kinds_dislike"]).append(k)
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
            print(f"learned from {m['n']} clips ({m['good']} 👍, {m['weak']} 👎, {m['meh']} meh). Trust {m['conf']:.0%} (cross-validated AUC {m.get('auc', 0.5):.2f}, lower end {m.get('auc_low', 0):.2f}, shuffled-ratings p {m.get('p', 1):.2f}). Strongest signals: {top_features(m)}")
        else:
            print(f"Not learning yet ({len(rows)} clips; needs {MIN_TOTAL} clips with at least {MIN_GOOD} 👍 and {MIN_RATED} rated 👍 or 👎).")
    else:
        print(report(rows) if rows else "No clips with saved signals yet.")
