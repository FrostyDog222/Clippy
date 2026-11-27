"""Small pure rules of the clip-deciding logic ("polish 1", 2026-11-22), kept apart so they can be tested. The old behaviour is in backup_logic_2026-11-22\\.

 * cap_z: a z-score has no upper limit, so one event in a long video (or a chat raid) could be worth +12 points. Dense signals are capped at 6 sigma, sparse ones at 3.5.
 * viral_penalty: the old rule was a cliff (4.99 cost 2 points, 5.0 cost none). Now the penalty grows smoothly from 0 at 5.0 to 2 at 4.0 and below.
 * talk_blocks: "chat, did you see that?! no way, chat!" is a reaction, not talk to chat. Talking to chat only drops a moment when nobody is really reacting.
 * no_ai_baseline: without any AI read the rank started from a fixed 5 and could never reach the default bar of 7; the viral score is the base instead.
 * total_penalty: stacked penalties are capped.
"""
import re

import numpy as np

CAP_DENSE, CAP_SPARSE = 6.0, 3.5
PENALTY_CAP = 4.0


def cap_z(z, sparse=False):
    """Clip a z-score array to [0, cap]: never negative, never more than the cap."""
    return np.clip(z, 0, CAP_SPARSE if sparse else CAP_DENSE)


def viral_penalty(score):
    """0 at a viral score of 5 or more, rising smoothly to 2.0 at 4 or less."""
    return round(2.0 * min(1.0, max(0.0, 5.0 - float(score))), 2)


def talk_blocks(talk_hits, strong_reaction):
    """True = drop the moment as talk to chat. Two or more talk words only drop it when there is no real reaction (excited voice, laughter, swearing) in it."""
    return talk_hits >= 2 and not strong_reaction


def no_ai_baseline(score):
    """The 0-10 'AI read' stand-in when no AI judged the moment: a bit more than the viral score, never above 10."""
    return min(10.0, 1.2 * float(score))


def total_penalty(pen):
    return min(PENALTY_CAP, sum(p for p, _ in pen))


def ai_base(win_score, frame_score, c_ai, drop_frame, viral):
    """The 0-10 base of the rank: the mean of the AI reads that count. The frame look is left out (drop_frame) when it was already used as a penalty, or when a strong spoken bit
    overrode it (a dark scene, a player looking at the ground, another game: three frames often show nothing while the streamer says the funny thing). No AI read at all: viral-score stand-in."""
    ai = [x for x in (win_score, None if drop_frame else frame_score, c_ai) if x is not None]
    return sum(ai) / len(ai) if ai else no_ai_baseline(viral)


def frame_overridden(win):
    """A spoken bit overrides the frame look only when the streamer is the one speaking (not the game's own dialogue)."""
    return bool(win) and win.get("speaker") != "game"


FACE_OK = 0.75      # share of frames whose webcam panel shows a face
FACE_SEEN = 0.15    # at least this share must be a CONFIDENT face, or the panel is not a webcam at all


def keep_webcam(cov, lenient):
    """Keep the webcam layout? cov = share of frames with a confident face (detector score 0.7); lenient = share of frames where a face is found at the SAME place with a lower score
    (0.3). A streamer who leans in and out of the frame (half a face is not a face to the detector) keeps his webcam layout; a panel that shows the game or a blank scene does not."""
    return cov >= FACE_OK or (cov >= FACE_SEEN and lenient >= FACE_OK)


def blur_crop_x(cam, side_zoom=0.75):
    """The blurred layout crops the middle `side_zoom` of the frame (0.125 off each side). A webcam that straddles that cut is shown half: shift the crop so the cam is outside it
    (or return None to keep the default, centred crop). cam = (x, y, w, h) as fractions of the frame."""
    x, w = float(cam[0]), float(cam[2])
    lo = (1 - side_zoom) / 2
    hi = 1 - lo
    if x < lo < x + w:           # the left cut goes through the webcam: start the crop after it
        return round(min(1 - side_zoom, x + w + 0.012), 4)
    if x < hi < x + w:           # the right cut goes through it: end the crop before it
        return round(max(0.0, x - 0.012 - side_zoom), 4)
    return None


# ---- polish 2 (2026-11-22), from the user's own 41 ratings -------------------------------------------------------------------------------------------------------
FRAME_OVERRIDE = False  # polish 1 dropped the frame score from the mean when a strong bit overrode a failed frame check. The ratings say the opposite (frames 3/10: 1 liked, 2 disliked), so it is off.


def frame_overridden(win):  # noqa: F811  (replaces the polish 1 version)
    return FRAME_OVERRIDE and bool(win) and win.get("speaker") != "game"


def frame_adjust(frame_score):
    """The picture score was the best predictor of a 👍 (8-9/10: 16 liked, 3 disliked; 7/10: 7 liked, 9 disliked): 0 at 7, +0.4 per point above (max +0.8), -0.4 per point below (max -0.8)."""
    if frame_score is None:
        return 0.0
    return round(0.4 * (min(9.0, max(5.0, float(frame_score))) - 7.0), 2)


def length_adjust(seconds):
    """Length prior from the ratings: 20-30 s was liked 74% of the time (14 of 19), under 20 s 2 of 6, 30-40 s 3 of 8. Small, so a great moment still wins at any length."""
    s = float(seconds or 0)
    if 20 <= s <= 30:
        return 0.4
    if s < 18:
        return -0.5
    if 30 < s <= 40:
        return -0.2
    return 0.0


def norm_channel(name):
    """One key per streamer: 'ohnePixel' / 'ohnepixel', 'caseoh_' / 'Caseoh_ VODS' were counted as different streamers in the learning."""
    import re
    k = re.sub(r"[^a-z0-9]", "", str(name or "").lower())
    for suf in ("vods", "vod", "clips", "clip", "live", "tv", "official"):
        if k.endswith(suf) and len(k) > len(suf) + 2:
            k = k[: -len(suf)]
    return k


# ---- polish 5: humour (2026-11-22) -------------------------------------------------------------------------------------------------------------------------------
def quote_found(text, quote, need=0.7):
    """Is the 'funniest line' the AI quoted really in the transcript of the window? (A made-up quote is no evidence of a joke.) Fuzzy: at least `need` of the quote must appear as one run."""
    import re
    from difflib import SequenceMatcher
    norm = lambda s: re.sub(r"[^\w' ]+", " ", str(s).lower()).split()  # \w keeps Cyrillic, Arabic, CJK... letters
    q, t = " ".join(norm(quote)), " ".join(norm(text))
    if len(q.split()) < 2 or not t:
        return False
    m = SequenceMatcher(None, t, q, autojunk=False).find_longest_match(0, len(t), 0, len(q))
    return m.size >= need * len(q)


# polish 6: the kinds of moments people clip. One list, used by the AI judge, the whole-video scan, the titles and the learning card.
KINDS = {
    "funny": "a joke, irony, an absurd or awkward moment, a mishap or glitch, physical or situational comedy, a reaction that lands (no punchline needed)",
    "drama": "an argument, beef, a call-out or roast, rage, a heated or awkward exchange between people (streamer, chat, guests, other players)",
    "shock": "something unexpected: a twist, a jump scare, a shocking reveal or event, a stunned reaction",
    "skill": "an impressive play, a clutch, a perfect stunt, a big win",
    "fail": "an epic fail, a costly mistake, bad luck, getting destroyed",
    "emotional": "genuine emotion: a wholesome, heartfelt, proud or sad moment, a big celebration",
    "interesting": "a notable statement, a hot take, news, a reveal or a fact people would share",
}
KIND_NAMES = list(KINDS) + ["neither"]


def kinds_text():
    return "; ".join(f"{k.upper()}: {v}" for k, v in KINDS.items())


PLACEHOLDERS = {"", "n/a", "na", "none", "nothing", "empty", "no", "-", "null", "unknown", "nothing remarkable", "not applicable"}
NO_KIND = {"none", "no", "n/a", "na", "nothing", "null", "not clip-worthy", "not worth it"}


def norm_kind(kind):
    """The AI's kind, made safe: one of KIND_NAMES, "neither" for a 'none / n/a', or "" (= unknown: no cap at all). A copied template ("funny|drama|...") is unknown."""
    k = str(kind or "").strip().lower()
    if k in KIND_NAMES:
        return k
    if not k or "|" in k:
        return ""
    if k in NO_KIND:
        return "neither"
    for name in KIND_NAMES:  # "drama moment", "a fail" ...
        if re.search(r"\b" + name + r"\b", k):
            return name
    return ""


def real_what(what):
    """A description that says something: at least 2 words and not a placeholder such as 'n/a' or 'none'."""
    w = str(what or "").strip().lower().strip(".!")
    return w not in PLACEHOLDERS and len(w.split()) >= 2


def kind_cap(score, kind, quote_found, what="", reacts=False):
    """Any kind of moment can score high when the AI can show it: a quoted line that is really in the transcript, a short description of what happens, or an audible reaction.
    A claim with nothing to show is capped at 5; 'neither' at 4; kind "" (a model that ignores the field) changes nothing."""
    s = float(score)
    kind = str(kind or "").strip().lower()
    if not kind:
        return s
    if kind == "neither":
        return min(s, 4.0)
    return s if (quote_found or real_what(what) or reacts) else min(s, 5.0)


def wilson(k, n, z=1.96):
    """Wilson interval for k liked out of n rated: (low, high) in 0..1. A bare percentage from a handful of clips says almost nothing."""
    if n <= 0:
        return (0.0, 1.0)
    p = k / n
    c = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / c
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / c
    return (max(0.0, centre - half), min(1.0, centre + half))


# ---- clip edges (2026-11-27): no dead moments, an opening that starts, cuts on sentence boundaries ---------------------------------------------------------------
# moments.py saves alive.json (one 0/1 per second: someone is speaking, or a loud jump, an excited voice, laughter, swearing or real action). These rules read it.
ALIVE_MIN_SHARE = 0.70   # at least 70% of the seconds of a clip must be alive
MAX_DEAD_RUN = 6.0       # no stretch of this many seconds in a row with nothing happening
OPEN_SECONDS, OPEN_MIN_ALIVE = 3, 2   # the first 3 seconds need 2 alive ones
SENT_GAP = 0.7           # a pause this long between two words ends a sentence, like . ! ?
EDGE_MAX_EARLIER, EDGE_MAX_LATER = 4.0, 3.0   # how far an edge may move to reach a sentence boundary


def alive_stats(alive, start, end):
    """(share of alive seconds, longest run of dead seconds) of the window [start, end) in a per-second 0/1 list."""
    a = [1 if x else 0 for x in alive[max(0, int(start)):max(0, int(end + 0.999))]]
    if not a:
        return 1.0, 0.0
    run = best = 0
    for x in a:
        run = 0 if x else run + 1
        best = max(best, run)
    return sum(a) / len(a), float(best)


def dead_moment(alive, start, end):
    """None when the window is alive enough, else a plain reason (the streamer is quiet and nothing happens: driving, walking, a menu)."""
    share, run = alive_stats(alive, start, end)
    if share < ALIVE_MIN_SHARE:
        return f"only {share:.0%} of it has speech, a reaction or action"
    if run >= MAX_DEAD_RUN:
        return f"{run:.0f} seconds in a row with nothing happening"
    return None


def fix_opening(alive, start, end, min_len):
    """(new_start, note): the first 3 seconds must start something. When they do not, the clip starts at the first alive second instead (at most 8 s later and only while
    min_len remains). (None, reason) = skip this moment."""
    s = int(start)
    if sum(1 if x else 0 for x in alive[s:s + OPEN_SECONDS]) >= OPEN_MIN_ALIVE or s >= len(alive):
        return start, None
    for t in range(s + 1, min(int(end) - 1, s + 9)):
        if sum(1 if x else 0 for x in alive[t:t + OPEN_SECONDS]) >= OPEN_MIN_ALIVE:
            if end - t >= min_len:
                return float(t), f"slow start cut off ({t - start:.0f} s)"
            break
    return None, "the first seconds are quiet and nothing starts"


def _sentence_end(words, i):
    """True when a sentence ends after words[i]: . ! ? at the end of it, a pause of 0.7 s or more, or it is the last word."""
    t = words[i]["w"].strip()
    return t[-1:] in ".!?" or i + 1 >= len(words) or words[i + 1]["s"] - words[i]["e"] >= SENT_GAP


def snap_to_speech(words, start, end, min_len, max_len, dur=None):
    """Move the edges of a clip to speech boundaries: never inside a word, and not in the middle of a sentence (starting at 'and then he...' or stopping at 'so I was like').
    Returns (start, end, notes). The start moves to the beginning of its sentence (at most 4 s earlier, else the next sentence at most 3 s later), the end to the end of its
    sentence (at most 4 s later, else back to the previous sentence end); the length always stays within [min_len, max_len]."""
    notes = []
    if not words:
        return start, end, notes
    n = len(words)
    i = next((k for k in range(n) if words[k]["e"] > start), None)  # first word that is not over before the start
    if i is not None and words[i]["s"] < end:
        if words[i]["s"] < start:                                   # inside a word: begin at its first letter
            ns = max(0.0, words[i]["s"] - 0.1)
            if end - ns <= max_len:
                start = ns; notes.append("start moved out of a word")
        if i > 0 and not _sentence_end(words, i - 1) and words[i]["s"] - start < 0.6:   # in the middle of a sentence
            k = i
            while k > 0 and not _sentence_end(words, k - 1):
                k -= 1
            ns = max(0.0, words[k]["s"] - 0.1)
            if start - ns <= EDGE_MAX_EARLIER and end - ns <= max_len:
                start = ns; notes.append("start moved to the beginning of the sentence")
            else:
                m = i
                while m < n and not _sentence_end(words, m - 1):
                    m += 1
                if m < n and words[m]["s"] - 0.1 - start <= EDGE_MAX_LATER and end - (words[m]["s"] - 0.1) >= min_len:
                    start = words[m]["s"] - 0.1; notes.append("start moved to the next sentence")
    j = next((k for k in range(n - 1, -1, -1) if words[k]["s"] < end), None)   # last word that starts before the end
    if j is not None and words[j]["e"] > start:
        if words[j]["e"] > end:                                     # inside a word: finish it
            ne = words[j]["e"] + 0.25
            if ne - start <= max_len and (dur is None or ne <= dur):
                end = ne; notes.append("end moved out of a word")
        if not _sentence_end(words, j) and words[j + 1]["s"] - words[j]["e"] < 0.8:     # the sentence carries on after the end
            m = j
            while m < n - 1 and not _sentence_end(words, m):
                m += 1
            ne = words[m]["e"] + 0.3
            if ne - end <= EDGE_MAX_EARLIER and ne - start <= max_len and (dur is None or ne <= dur):
                end = ne; notes.append("end moved to the end of the sentence")
            else:
                p = next((k for k in range(j - 1, -1, -1) if _sentence_end(words, k)), None)
                if p is not None and words[p]["e"] + 0.3 - start >= min_len:
                    end = words[p]["e"] + 0.3; notes.append("end moved back to the end of the previous sentence")
    return round(start, 2), round(end, 2), notes


# A viewer-made Twitch clip (or any short source) can simply end in the middle of a sentence ("... puts family-friendly destruction to a whole"): there is no more footage to finish it.
DANGLING_LAST = {"a", "an", "the", "to", "of", "in", "on", "at", "for", "with", "from", "into", "by", "and", "but", "or", "so", "because", "if", "when", "while", "that", "which", "who",
                 "than", "as", "is", "are", "was", "were", "be", "been", "have", "has", "had", "will", "would", "can", "could", "my", "your", "his", "her", "their", "our", "like", "gonna", "about"}
DANGLING_PREV = {"a", "an", "the", "to", "of", "in", "on", "at", "for", "with", "from", "into", "my", "your", "his", "her", "their", "our"}   # "to a whole" / "in the": a noun is missing


def unfinished_tail(words, start, end, src_dur, min_len):
    """When the speech runs right up to the end of the source and stops mid-sentence (no . ! ? and the last words are dangling: "to a whole", "and the", "because"),
    return (new_end, note): the end of the last finished sentence when at least min_len seconds remain, else (None, reason) = skip this moment. (end, None) = fine."""
    if not words or not src_dur:
        return end, None
    ws = [k for k, w in enumerate(words) if start <= w["s"] < end]
    if not ws:
        return end, None
    j = ws[-1]
    last = words[j]
    t = last["w"].strip()
    if last["e"] < src_dur - 0.7 or t[-1:] in ".!?":
        return end, None
    low = re.sub(r"[^\w']+", "", t.lower())
    prev = re.sub(r"[^\w']+", "", words[j - 1]["w"].lower()) if j > 0 else ""
    if low not in DANGLING_LAST and prev not in DANGLING_PREV:
        return end, None
    p = next((k for k in range(j - 1, -1, -1) if words[k]["s"] >= start and _sentence_end(words, k)), None)
    if p is not None and words[p]["e"] + 0.3 - start >= min_len:
        return round(words[p]["e"] + 0.3, 2), "the source stops in the middle of a sentence: ended on the last finished one"
    return None, "the source stops in the middle of a sentence and there is no finished one to end on"
