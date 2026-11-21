"""What Clippy looks for. Config "topic" (default "GTA 6"; any game or subject, e.g. "Minecraft", "Elden Ring") and "trending_any" (look for whatever is trending
right now, no topic needed). Every script asks this module instead of hard-coding a game.
info(cfg) -> {"name", "gta", "any", "tag", "matches"(title -> bool), "queries"(list of YouTube searches), "creator_query"(creator -> search), "live_query"}"""
import json, pathlib, re

R = pathlib.Path(__file__).resolve().parent.parent
GTA_RX = re.compile(r"gta\s*(6|vi)|grand theft auto (6|vi)|vice city|leonida", re.I)
ANY_QUERIES = ("funny moments stream", "stream highlights", "insane moments", "best moments")  # generic searches: what is popular right now, whatever it is


def load():
    try:
        return json.loads((R / "autopost_config.json").read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def info(cfg=None):
    cfg = cfg if cfg is not None else load()
    name = str(cfg.get("topic", "GTA 6") or "").strip()[:60]
    anyt = bool(cfg.get("trending_any"))
    gta = bool(re.search(r"\bgta\b|grand theft", name, re.I))
    words = [w for w in re.findall(r"[a-z0-9]+", name.lower()) if len(w) > 1 or w.isdigit()]

    def matches(title):
        if anyt or not words:  # no topic (or "trending: anything"): every title passes the topic filter
            return True
        t = (title or "").lower()
        return bool(GTA_RX.search(t)) if gta else all(w in t for w in words)

    return {"name": name or "gaming", "gta": gta, "any": anyt, "tag": "#" + (re.sub(r"[^A-Za-z0-9]", "", name) or "Gaming"), "matches": matches,
            "queries": list(ANY_QUERIES) if (anyt or not name) else [f"{name} gameplay", f"{name} reaction", f"{name} stream", name],
            "creator_query": (lambda c: f"{c} {name}".strip()) if name else (lambda c: f"{c} funny moments"),
            "live_query": name or "live"}
