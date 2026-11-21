"""Post one finished clip WITHOUT Claude: template text -> upload (Chrome script or official API per config) -> record -> progress.
python scripts/post_clip.py <path\\to\\clip_N.mp4> [--mode auto|private|public]
auto = follow autopost_config.json private_days. Uploader comes from config "uploader" (script|api; "claude" falls back to script)."""
import argparse, datetime as dt, json, os, pathlib, subprocess, sys

R = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(R))
PY = str(R / ".venv" / "Scripts" / "python.exe")


def progress(pct, msg):
    subprocess.run([PY, str(R / "scripts" / "progress.py"), str(pct), msg])


sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from friendly import friendly  # noqa: E402
ap = argparse.ArgumentParser(); ap.add_argument("clip"); ap.add_argument("--mode", choices=["auto", "private", "public"], default="auto")
ap.add_argument("--only", choices=["youtube", "tiktok"])
a = ap.parse_args()
clip = pathlib.Path(a.clip).resolve(); cj = clip.with_suffix(".json"); mj = clip.with_name(clip.name.replace("clip_", "meta_")).with_suffix(".json")
cfg = json.loads((R / "autopost_config.json").read_text(encoding="utf-8"))
_yt, _tt = cfg.get("post_youtube", True), cfg.get("post_tiktok", True)  # dashboard switches: where to post
if not a.only:
    if not _yt and not _tt:
        progress(-1, "Stopped: posting is switched OFF for both YouTube and TikTok (dashboard)"); print(json.dumps({"error": "both platforms are switched off in the dashboard: not posted"})); sys.exit(7)
    if _yt != _tt:
        a.only = "youtube" if _yt else "tiktok"
import ytlimit  # noqa: E402
_skip_note = None
if ytlimit.until():  # YouTube refused an upload for its daily limit a while ago and has not reset yet: do not try again, post to TikTok only
    _when = ytlimit.text(cfg.get("time_format", "12") == "12")
    if a.only == "youtube" or (a.only is None and not _tt):
        progress(-1, f"YouTube's daily upload limit is still active (until about {_when}): not posted"); print(json.dumps({"error": f"YOUTUBE_DAILY_LIMIT: still active until about {_when}, not posted"})); sys.exit(7)
    if a.only is None and _yt and _tt:
        a.only = "tiktok"; _skip_note = f"YouTube skipped: its daily upload limit is active until about {_when}"
if os.environ.get("CLIPDESK_FORCE_MODE") in ("private", "public"):  # tests: forces the visibility no matter what the config says (a config edit once got overwritten and a test went public)
    mode = os.environ["CLIPDESK_FORCE_MODE"]
elif a.mode == "auto":
    import tick
    start = dt.date.fromisoformat(cfg["start_date"])
    mode = "private" if dt.date.today() < start + dt.timedelta(days=int(cfg["private_days"])) else "public"
else:
    mode = a.mode
progress(10, f"Writing title and description ({mode})")
if not mj.exists():
    subprocess.run([PY, str(R / "scripts" / "make_meta.py"), str(cj)], check=True, capture_output=True)
meta = json.loads(mj.read_text(encoding="utf-8")); clipinfo = json.loads(cj.read_text(encoding="utf-8"))
import re  # noqa: E402
LOCAL = re.compile(r"[A-Za-z]:[\\/]|[\\/]Users[\\/]|jobs[\\/]|file://|\.mp4\b|\bclip_\d|\bsource\.", re.I)  # a local path or file name must never reach a public post
if any(LOCAL.search(str(meta.get(k, ""))) for k in ("yt_title", "yt_description", "tiktok_caption")):
    progress(-1, "Stopped: the title/description contains a local file path or file name"); print(json.dumps({"error": "metadata contains a local path or file name: not posted"})); sys.exit(6)
tool = "api_upload.py" if cfg.get("uploader") == "api" else "upload.py"
progress(30, f"Uploading with {tool}")
r = subprocess.run([PY, str(R / "scripts" / tool), "post", str(clip), "--title", meta["yt_title"], "--description", meta["yt_description"],
                    "--caption", meta["tiktok_caption"], "--mode", mode] + (["--only", a.only] if a.only else []), capture_output=True, text=True, encoding="utf-8")
try:
    res = json.loads(r.stdout.strip().splitlines()[-1])
except Exception:
    res = {"error": f"no result from {tool}: {(r.stdout + r.stderr)[-300:]}"}
if "YOUTUBE_DAILY_LIMIT" in str(res.get("error")):
    ytlimit.set_hit()  # remembered: the next posts go to TikTok only until YouTube's day resets
if _skip_note:
    res["note"] = _skip_note
if res.get("error") and not (res.get("youtube") or res.get("tiktok")):
    progress(-1, friendly(res["error"])[:200]); print(json.dumps(res)); sys.exit(r.returncode or 5)  # nothing was posted
# partial success (e.g. YouTube ok, TikTok failed) is still recorded below so it is never lost or duplicated
sf = R / "autopost_state.json"
st = json.loads(sf.read_text(encoding="utf-8")) if sf.exists() else {"used_video_ids": [], "posts": [], "consecutive_failures": 0}
vid = (clipinfo.get("source_url") or "").split("v=")[-1].split("&")[0]
if vid and vid not in st["used_video_ids"]:
    st["used_video_ids"].append(vid)
now = dt.datetime.now()
st["posts"].append({"date": now.strftime("%Y-%m-%d"), "ts": now.isoformat(timespec="seconds"), "slot": 0, "channel": clipinfo.get("source_channel"),
                    "url": clipinfo.get("source_url"), "mode": mode, "youtube_link": res.get("youtube"), "tiktok_status": res.get("tiktok"),
                    "title": meta["yt_title"], "caption": meta["tiktok_caption"], "method": tool, "error": res.get("error"),
                    "layout": clipinfo.get("layout"), "origin": clipinfo.get("origin", "video"), "score": clipinfo.get("score"), "seconds": clipinfo.get("seconds"),
                    "on_video": clipinfo.get("title"), "creator": clipinfo.get("creator")})
st["consecutive_failures"] = 0
sf.write_text(json.dumps(st, indent=1), encoding="utf-8")
with open(R / "autopost_log.md", "a", encoding="utf-8") as f:
    f.write(f"- {now:%Y-%m-%d %H:%M} posted ({mode}, {tool}): {meta['yt_title']} | YT {res.get('youtube')} | TikTok {res.get('tiktok')}\n")
if res.get("error"):
    progress(-1, "Partly posted: " + friendly(res["error"])[:150])
else:
    progress(100, f"Done: {meta['yt_title']}")
print(json.dumps(res)); sys.exit(5 if res.get("error") else 0)
