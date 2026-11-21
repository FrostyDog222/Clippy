"""Create autopost_config.json with safe defaults if it does not exist yet (used by setup.bat)."""
import datetime as dt, json, pathlib
f = pathlib.Path(__file__).resolve().parent.parent / "autopost_config.json"
if not f.exists():
    f.write_text(json.dumps({
        "enabled": True, "autopilot": False, "uploader": "claude", "posts_per_day": 3, "window_start": "09:00", "window_end": "22:30",
        "min_gap_minutes": 120, "creator_mode": "rotate",
        "creators": ["ohnePixel", "DarkViperAU", "IShowSpeed", "TheBurntPeanut", "Kai Cenat", "xQc", "Adin Ross", "Duke Dennis"],
        "max_per_creator_per_day": 1, "clip_seconds": 30, "layout": "auto", "title_writer": "template", "languages": ["en"], "use_trending_fallback": True,
        "start_date": dt.date.today().isoformat(), "private_days": 0, "quality": "strict"}, indent=2), encoding="utf-8")
    print("created autopost_config.json")
else:
    print("autopost_config.json already exists, left unchanged")
