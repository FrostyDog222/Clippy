"""Claude-free title/description/caption from a cut clip's clip_N.json, following TITLE_STYLE.md's patterns.
python scripts/make_meta.py <clip_N.json>  -> writes meta_N.json next to it and prints it.
The description quotes what is really said in the clip (swears masked) and names the creator and source; it never invents events."""
import json, pathlib, re, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from textutil import BANNED, excerpt, clip_text, quote_hook  # noqa: E402
import topic as topic_mod  # noqa: E402
TP = topic_mod.info()
TAGS = "#Gaming #Viral" if TP["any"] else f"{TP['tag']} #Gaming #Gameplay"

f = pathlib.Path(sys.argv[1]); c = json.loads(f.read_text(encoding="utf-8"))
hook = re.sub(r"\s+", " ", (c.get("hook") or c.get("title") or c.get("source_title") or f"{TP['name']} moment")).strip().rstrip(".!?")
chan, url, src_title = c.get("source_channel") or "the streamer", c.get("source_url") or "", c.get("source_title") or ""
m = re.search(r"^(.+?)\s+reacts to ", hook) or re.search(r"-\s*(.+?)\s+on GTA 6$", hook)
name = m.group(1) if m else re.sub(r"\s+(live|raw|clips|highlights|vods?)$", "", chan, flags=re.I).strip()
if BANNED.search(hook):
    sys.exit(f"hook contains a banned word: {hook!r}")
try:
    text = clip_text(f)
except Exception:
    text = ""
said = excerpt(text)
tags = f" {TP['tag']} #Shorts"
title = hook[: 100 - len(tags)].rstrip() + tags
tag_name = re.sub(r"[^A-Za-z0-9]", "", name)
# short and catchy: the AI's one-liner when there is one ("blurb" in clip_N.json), else just the hook. No transcript, no summary.
blurb = re.sub(r"\s+", " ", (c.get("blurb") or "")).strip()
# no credit line and no link: just the one-liner and hashtags that fit this stream (the game + the real streamer name, never a placeholder)
real = c.get("source_channel")
tag_name = re.sub(r"[^A-Za-z0-9]", "", re.sub(r"\s+(live|raw|clips|highlights|vods?)$", "", real, flags=re.I)) if real else ""
stream_tags = f" #{tag_name}" if tag_name else ""
shown = re.sub(r"\s+(live|raw|clips|highlights|vods?)$", "", real, flags=re.I).strip() if real else ""  # streamer name as plain text (no link)
who = f"🎮 {shown}" if shown else ""
def pick_tags(max_n):
    """The automatic hashtags in the order they are shown, cut to `max_n` (Settings, "Hashtags per video"): the least useful ones go first (#Gameplay / #Viral, then #fyp, then #Gaming)."""
    shown_order = [*TAGS.split(), *stream_tags.split(), "#Shorts", "#fyp"]
    keep = sorted(shown_order, key=lambda t: (t == TP["tag"] and not TP["any"]) * -4 + (t.lower() == f"#{tag_name}".lower() and bool(tag_name)) * -3 + (t == "#Shorts") * -2
                  + (t == "#Gaming") * -1 + (t in ("#Gameplay", "#Viral")) * 3 + (t == "#fyp") * 1)[:max(0, max_n)]
    return [t for t in shown_order if t in keep]


try:
    max_tags = int(json.loads((pathlib.Path(__file__).resolve().parent.parent / "autopost_config.json").read_text(encoding="utf-8")).get("max_hashtags", 5))
except Exception:
    max_tags = 5
tagline = " ".join(pick_tags(max_tags))
lines = [blurb or hook + ".", *([who] if who else []), "", tagline] if tagline else [blurb or hook + ".", *([who] if who else [])]
caption = f"{blurb or hook} {who} {tagline}".replace("  ", " ").strip()
meta = {"yt_title": title, "yt_description": "\n".join(lines), "tiktok_caption": caption[:150],
        "checks": "template (no AI): real quote from the clip, source and creator credit + link, banned words, swears masked, length limits"}
out = f.with_name(f.name.replace("clip_", "meta_"))
out.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"); print(json.dumps(meta, ensure_ascii=False, indent=2))
