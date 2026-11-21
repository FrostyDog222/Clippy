"""Is an autopost due right now? Exit 0 + 'DUE mode=private|public' or exit 1 + 'NOT DUE: reason'. No LLM, no network."""
import datetime as dt, json, pathlib, sys

R = pathlib.Path(__file__).parent


def decide(cfg, posts, now, paused=False):
    if paused or not cfg.get("enabled", True):
        return False, "paused or disabled", None
    today = now.strftime("%Y-%m-%d")
    mine = [p for p in posts if p.get("date") == today and not p.get("deleted")]  # posts the user deleted do not count toward the limit
    if len(mine) >= int(cfg["posts_per_day"]):
        return False, f"daily limit reached ({len(mine)}/{cfg['posts_per_day']})", None
    hm = now.strftime("%H:%M")
    if not (cfg["window_start"] <= hm <= cfg["window_end"]):
        return False, f"outside window {cfg['window_start']}-{cfg['window_end']}", None
    stamps = [dt.datetime.fromisoformat(p["ts"]) for p in mine if p.get("ts")]
    if stamps and (now - max(stamps)).total_seconds() < int(cfg["min_gap_minutes"]) * 60:
        return False, f"gap < {cfg['min_gap_minutes']} min since last post", None
    start = dt.date.fromisoformat(cfg["start_date"])
    private = now.date() < start + dt.timedelta(days=int(cfg["private_days"]))
    return True, "due", "private" if private else "public"


def selftest():
    cfg = {"enabled": True, "posts_per_day": 2, "window_start": "09:00", "window_end": "22:00",
           "min_gap_minutes": 60, "start_date": "2026-11-19", "private_days": 3}
    n = dt.datetime(2026, 11, 19, 12, 0)
    assert decide(cfg, [], n)[0:3:2] == (True, "private")
    assert not decide(cfg, [], n.replace(hour=23))[0]
    assert not decide(cfg, [{"date": "2026-11-19", "ts": "2026-11-19T11:30:00"}], n)[0]
    assert decide(cfg, [{"date": "2026-11-19", "ts": "2026-11-19T10:30:00"}], n)[0]
    two = [{"date": "2026-11-19", "ts": "2026-11-19T09:30:00"}] * 2
    assert not decide(cfg, two, n)[0]
    assert decide(cfg, [], n.replace(day=23))[2] == "public"
    assert not decide(cfg, [], n, paused=True)[0]
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest(); sys.exit(0)
    cfg = json.loads((R / "autopost_config.json").read_text(encoding="utf-8"))
    sf = R / "autopost_state.json"
    posts = json.loads(sf.read_text(encoding="utf-8")).get("posts", []) if sf.exists() else []
    ok, why, mode = decide(cfg, posts, dt.datetime.now(), (R / "PAUSED.txt").exists())
    print(f"DUE mode={mode}" if ok else f"NOT DUE: {why}")
    sys.exit(0 if ok else 1)
