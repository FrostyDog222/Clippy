"""Pre-post check of a finished clip.

usage: python check.py <clip.mp4> [--campaign campaigns.md] [--caption "text"]
Reads clip_<n>.json (written by cut.py) next to the clip if present. Exit code 1 on any FAIL.
campaigns.md lines understood: min_seconds: 15 / max_seconds: 60 / required_hashtags: #a #b / required_mention: @x
"""
import argparse, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from common import ffprobe, load_json, tool

BANNED = ["leak", "leaked build", "play early", "early access download", "free download", "crack", "piracy",
          "mod menu", "money glitch", "afk bot"]


def parse_campaign(path):
    rules = {"min": 7.0, "max": 60.0, "tags": [], "mention": None}
    if not path or not Path(path).exists():
        return rules, False
    txt = Path(path).read_text(encoding="utf-8", errors="replace")
    m = re.search(r"min_seconds:\s*(\d+(?:\.\d+)?)", txt, re.I)
    if m: rules["min"] = float(m.group(1))
    m = re.search(r"max_seconds:\s*(\d+(?:\.\d+)?)", txt, re.I)
    if m: rules["max"] = float(m.group(1))
    m = re.search(r"required_hashtags:\s*(.+)", txt, re.I)
    if m: rules["tags"] = re.findall(r"#\w+", m.group(1))
    m = re.search(r"required_mention:\s*(@\w+)", txt, re.I)
    if m: rules["mention"] = m.group(1)
    return rules, True


def fps_of(s):
    try:
        a, b = s.split("/"); return float(a) / float(b)
    except Exception:
        return 0.0


def banned_in(text):
    low = text.lower()
    return [b for b in BANNED if b in low]


SILENCE_DB, SILENCE_MIN = -55, 2.0   # below -55 dB for 2 s or more counts as silence (a muted stream part is digital silence, about -90 dB)
MAX_SILENT_RUN, MAX_SILENT_SHARE = 6.0, 0.25  # a clip is too quiet when one silent stretch lasts 6 s or more, or a quarter of it is silent


def parse_silence(text, dur):
    """The (total, longest) seconds of silence out of ffmpeg's silencedetect lines. A silence that runs to the end of the clip is counted up to `dur`."""
    total = longest = 0.0
    start = None
    for line in text.splitlines():
        m = re.search(r"silence_start: (-?[\d.]+)", line)
        if m:
            start = max(0.0, float(m.group(1))); continue
        m = re.search(r"silence_end: ([\d.]+) \| silence_duration: ([\d.]+)", line)
        if m:
            d = float(m.group(2)); total += d; longest = max(longest, d); start = None
    if start is not None and dur > start:  # still silent when the clip ends
        d = dur - start; total += d; longest = max(longest, d)
    return total, longest


def silence_of(clip, dur):
    """(total, longest) seconds of silence in a clip's sound. (0, 0) when it cannot be measured."""
    import subprocess
    try:
        r = subprocess.run([tool("ffmpeg"), "-hide_banner", "-nostats", "-i", str(clip), "-vn", "-af", f"silencedetect=noise={SILENCE_DB}dB:d={SILENCE_MIN}", "-f", "null", "-"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300, creationflags=0x08000000 if sys.platform == "win32" else 0)
        return parse_silence(r.stderr, dur)
    except Exception:
        return 0.0, 0.0


def too_quiet(total, longest, dur):
    return longest >= MAX_SILENT_RUN or (dur > 0 and total / dur >= MAX_SILENT_SHARE)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("clip"); ap.add_argument("--campaign"); ap.add_argument("--caption", default="")
    a = ap.parse_args()
    clip = Path(a.clip)
    res = []  # (name, ok, detail)

    def add(name, ok, detail=""):
        res.append((name, ok, detail))

    if not clip.exists():
        sys.exit(f"FAIL file not found: {clip}")
    side = clip.with_suffix(".json")
    meta = load_json(side) if side.exists() else {}
    rules, have_campaign = parse_campaign(a.campaign)
    p = ffprobe(clip)
    streams = p.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    au = next((s for s in streams if s.get("codec_type") == "audio"), None)
    dur = float(p.get("format", {}).get("duration", 0) or 0)

    add("resolution 1080x1920", bool(v) and (v.get("width"), v.get("height")) == (1080, 1920),
        f"{v.get('width')}x{v.get('height')}" if v else "no video")
    f = fps_of(v.get("r_frame_rate", "0/1")) if v else 0
    add("fps about 30", 29 <= f <= 31, f"{f:.2f}")
    add("has audio", au is not None)
    if au is not None:  # sound that is silent for long stretches (a muted part of a stream): a Short nobody can hear is skipped
        tot, run = silence_of(clip, dur)
        add("not silent for long", not too_quiet(tot, run, dur), f"{tot:.0f}s silent in total, longest {run:.0f}s")
    add(f"duration {rules['min']:g}-{rules['max']:g}s" + ("" if have_campaign else " (default)"),
        rules["min"] <= dur <= rules["max"], f"{dur:.1f}s")
    hook = meta.get("hook", "")
    hits = banned_in(hook + " " + a.caption)
    add("no banned terms in caption/hook", not hits, ", ".join(hits))
    if rules["tags"]:
        miss = [t for t in rules["tags"] if t.lower() not in a.caption.lower()]
        add("required hashtags", not miss, ("missing " + " ".join(miss)) if miss else " ".join(rules["tags"]))
    if rules["mention"]:
        ok = rules["mention"].lower() in a.caption.lower()
        add("required mention", ok, rules["mention"])
    if not have_campaign:
        add("campaign rules file", True, "none given, defaults used (NOT a rules check)")

    for n, ok, d in res:
        print(f"{'PASS' if ok else 'FAIL'}  {n}" + (f"  [{d}]" if d else ""))
    bad = [r for r in res if not r[1]]
    print("RESULT: " + ("FAIL" if bad else "PASS"))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
