"""Small pure rules of the clip-deciding logic ("polish 1", 2026-11-22), kept apart so they can be tested. The old behaviour is in backup_logic_2026-11-22\\.

 * cap_z: a z-score has no upper limit, so one event in a long video (or a chat raid) could be worth +12 points. Dense signals are capped at 6 sigma, sparse ones at 3.5.
 * viral_penalty: the old rule was a cliff (4.99 cost 2 points, 5.0 cost none). Now the penalty grows smoothly from 0 at 5.0 to 2 at 4.0 and below.
 * talk_blocks: "chat, did you see that?! no way, chat!" is a reaction, not talk to chat. Talking to chat only drops a moment when nobody is really reacting.
 * no_ai_baseline: without any AI read the rank started from a fixed 5 and could never reach the default bar of 7; the viral score is the base instead.
 * total_penalty: stacked penalties are capped.
"""
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
