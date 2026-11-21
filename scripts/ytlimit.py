"""YouTube's daily upload limit: when YouTube refuses an upload because of it, remember until when (YouTube's day ends at midnight Pacific time), so Clippy sends
clips to TikTok only until then instead of failing again and again. Local file yt_limit.json. No network."""
import datetime as dt, json, time
from pathlib import Path

F = Path(__file__).resolve().parent.parent / "yt_limit.json"


def reset_time(now=None):
    """The next midnight in Pacific time as an epoch (falls back to 08:00 UTC = midnight PST when the time zone data is missing)."""
    now = now or time.time()
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("America/Los_Angeles")
        t = dt.datetime.fromtimestamp(now, tz)
        return (dt.datetime.combine(t.date() + dt.timedelta(days=1), dt.time(0, 0), tz)).timestamp() + 60
    except Exception:
        t = dt.datetime.fromtimestamp(now, dt.timezone.utc)
        nxt = dt.datetime.combine(t.date(), dt.time(8, 1), dt.timezone.utc)
        return (nxt if nxt.timestamp() > now else nxt + dt.timedelta(days=1)).timestamp()


def set_hit(now=None):
    until = reset_time(now)
    F.write_text(json.dumps({"until": until}), encoding="utf-8")
    return until


def until(now=None):
    """Epoch until which YouTube is limited, or None."""
    try:
        u = float(json.loads(F.read_text(encoding="utf-8"))["until"])
    except Exception:
        return None
    return u if u > (now or time.time()) else None


def text(w12=False, now=None):
    u = until(now)
    if not u:
        return None
    t = dt.datetime.fromtimestamp(u)
    return t.strftime("%I:%M %p").lstrip("0") if w12 else t.strftime("%H:%M")


def clear():
    F.unlink(missing_ok=True)
