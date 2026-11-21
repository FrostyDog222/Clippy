"""Does the SOURCE video already have burned-in subtitles / captions (added by the streamer's or the clipper's editor)? If yes, Clippy does not add its own on top (two sets of
subtitles on top of each other look bad). No network, no model: it looks at a handful of frames for a row of large, bright, letter-sized shapes in the lower part of the picture,
the way bold captions look; small in-game subtitles and HUD text are too small to count.

has_burned_captions(video, start, end) -> (bool, share_of_frames_with_a_caption_line)"""
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

LETTER_H = (0.024, 0.100)   # letter height as a share of the frame height (bold captions; the small in-game subtitle is about 0.02)
LETTER_W = (0.006, 0.120)   # letter (or word piece) width as a share of the frame width
MIN_LETTERS = 4             # shapes in one row that look like a text line
SHARE = 0.25                # a caption line in at least this share of the sampled frames


def caption_line(frame):
    """True when this frame has a row of caption-sized, bright (white / yellow) letter shapes in its lower part."""
    h, w = frame.shape[:2]
    y0, x0 = int(h * 0.50), int(w * 0.05)
    roi = frame[y0:int(h * 0.97), x0:int(w * 0.95)]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    hh, ss, vv = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    bright = ((vv > 225) & (ss < 55)) | ((hh >= 18) & (hh <= 38) & (ss > 110) & (vv > 190))  # white, or yellow (the usual caption colours)
    m = bright.astype(np.uint8) * 255
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    n, _, stats, cents = cv2.connectedComponentsWithStats(m, connectivity=8)
    boxes = []
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if LETTER_H[0] * h <= bh <= LETTER_H[1] * h and LETTER_W[0] * w <= bw <= LETTER_W[1] * w and 0.12 <= area / float(bw * bh) <= 0.90:
            boxes.append((cents[i][1], bh, x, bw))
    if len(boxes) < MIN_LETTERS:
        return False
    boxes.sort()
    best = 0
    for cy, bh, _, _ in boxes:  # letters of one line: same height (within a third) and almost the same vertical centre
        row = [b for b in boxes if abs(b[0] - cy) <= 0.025 * h and 0.65 <= b[1] / float(bh) <= 1.55]
        if len(row) >= MIN_LETTERS:
            span = max(b[2] + b[3] for b in row) - min(b[2] for b in row)
            if span >= 0.10 * w:
                best = max(best, len(row))
    return best >= MIN_LETTERS


def has_burned_captions(video, start=None, end=None, n=16):
    """(True/False, share of the sampled frames that show a caption line)."""
    try:
        import facecam
        frames = facecam.sample_frames(video, start, end, n)
    except Exception:
        return False, 0.0
    if len(frames) < 4:
        return False, 0.0
    share = sum(1 for f in frames if caption_line(f)) / len(frames)
    return share >= SHARE, round(share, 2)


if __name__ == "__main__":
    for v in sys.argv[1:]:
        print(v, has_burned_captions(v))
