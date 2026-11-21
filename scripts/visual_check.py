"""No-AI look at a moment: is something visibly happening on a gameplay screen? (used by autorun when no AI is available, and as the Strict gate then)
Samples 12 frames of [t0, t1] and measures: how much the picture changes (motion), how much is black, whether a big face fills the screen
(a webcam-only / just-chatting scene), and how many sampled frames are near-identical (a frozen menu / dialogue).
analyze(video, t0, t1) -> {"ok": bool, "reason": str, **metrics}.  python scripts/visual_check.py <video> [start end]"""
import pathlib, sys

import cv2
import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import facecam  # noqa: E402

N = 12
MIN_MOTION = 5.0      # mean change between sampled frames (0-255 gray) below this = static scene (menu, dialogue, standing around)
MAX_DARK = 0.4        # share of nearly black frames
MAX_FACE_SCREEN = 0.5  # share of frames where a face fills the screen (webcam only / just chatting)
MAX_STILL = 0.6       # share of consecutive pairs that are practically identical


def metrics(video, t0, t1):
    frames = facecam.sample_frames(video, t0, t1, N)
    if len(frames) < 6:
        return None
    gray = [cv2.cvtColor(cv2.resize(f, (160, 90)), cv2.COLOR_BGR2GRAY).astype(np.float32) for f in frames]
    dark = float(np.mean([g.mean() < 25 for g in gray]))
    # motion = change between frames ~0.1 s apart, measured in short bursts (frames seconds apart would only show scene changes)
    diffs = []
    cap = cv2.VideoCapture(str(video)); fps = cap.get(cv2.CAP_PROP_FPS) or 30; step = max(1, int(round(fps / 10)))
    for k in range(5):
        cap.set(cv2.CAP_PROP_POS_MSEC, (t0 + (t1 - t0) * (0.1 + 0.2 * k)) * 1000)
        prev = None
        for i in range(step * 4 + 1):
            ok, f = cap.read()
            if not ok:
                break
            if i % step == 0:
                g = cv2.cvtColor(cv2.resize(f, (160, 90)), cv2.COLOR_BGR2GRAY).astype(np.float32)
                if prev is not None:
                    diffs.append(float(np.abs(g - prev).mean()))
                prev = g
    cap.release()
    if not diffs:
        return None
    faces = facecam.detect_faces(frames)
    big = []
    for f, ds in zip(frames, faces):
        h, w = f.shape[:2]
        big.append(any(d[2] * d[3] > 0.10 * w * h or d[2] > 0.28 * w for d in ds))
    return {"motion": round(float(np.mean(diffs)), 2), "still": round(float(np.mean([d < 2.0 for d in diffs])), 2), "dark": round(dark, 2),
            "face_screen": round(float(np.mean(big)), 2), "frames": len(frames)}


def analyze(video, t0, t1):
    m = metrics(video, t0, t1)
    if m is None:
        return {"ok": None, "reason": "could not read frames"}
    why = None
    if m["dark"] >= MAX_DARK:
        why = "mostly black screen"
    elif m["face_screen"] >= MAX_FACE_SCREEN:
        why = "a face fills the screen (webcam / just chatting), no gameplay"
    elif m["still"] >= MAX_STILL or m["motion"] < MIN_MOTION:
        why = "the picture barely changes (menu, dialogue or standing still)"
    return {"ok": why is None, "reason": why or "gameplay with movement", **m}


if __name__ == "__main__":
    v = pathlib.Path(sys.argv[1]).resolve()
    a = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0
    b = float(sys.argv[3]) if len(sys.argv) > 3 else facecam.duration(v)
    print(analyze(v, a, b))
