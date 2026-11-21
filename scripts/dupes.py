"""Duplicate guard: never make the SAME moment of the SAME streamer twice (for example their VOD and their Twitch clip of one moment).
Several streamers playing the same game, or reacting to the same event, are NOT duplicates: only a moment from the same streamer counts, and only when
  (1) it is the same source video and the time ranges overlap, or
  (2) the words spoken are almost the same (different upload of the same moment).
Every clip that was made is remembered in seen_moments.jsonl (local only), also after you removed it. A link you pasted yourself ("Clip a link") is never skipped."""
import json, re, time
from pathlib import Path

F = Path(__file__).resolve().parent.parent / "seen_moments.jsonl"
KEEP_DAYS = 60
MIN_WORDS = 12     # fewer words say too little to compare
SIMILAR = 0.6      # share of the same words (Jaccard) that makes two transcripts "the same moment"


def norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def words(text):
    return set(w for w in re.findall(r"[a-z0-9']+", str(text or "").lower()) if len(w) > 2)


def overlap(a0, a1, b0, b1):
    inter = min(a1, b1) - max(a0, b0)
    short = min(a1 - a0, b1 - b0)
    return inter / short if short > 0 and inter > 0 else 0.0


def load(now=None):
    now, out = now or time.time(), []
    for ln in (F.read_text(encoding="utf-8").splitlines() if F.exists() else []):
        try:
            r = json.loads(ln)
            if now - r.get("t", 0) < KEEP_DAYS * 86400:
                out.append(r)
        except Exception:
            pass
    return out


def record(source_id, channel, start, end, text, now=None):
    with open(F, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"t": now or time.time(), "source": source_id, "who": norm(channel), "start": float(start), "end": float(end),
                             "text": " ".join(str(text or "").split()[:90])}, ensure_ascii=False) + chr(10))


def is_duplicate(source_id, channel, start, end, text, now=None):
    """The reason (plain words) when this moment was already made from the same streamer, else None."""
    who = norm(channel)
    if not who:
        return None
    mine = words(text)
    for r in load(now):
        if r.get("who") != who:
            continue  # another streamer: never a duplicate, even for the same game or the same event
        if r.get("source") == source_id and overlap(float(start), float(end), r["start"], r["end"]) >= 0.4:
            return "the same part of the same video was already made"
        theirs = words(r.get("text"))
        if len(mine) >= MIN_WORDS and len(theirs) >= MIN_WORDS and len(mine & theirs) / len(mine | theirs) >= SIMILAR:
            return "the same words from the same streamer were already made (another upload of the same moment)"
    return None
