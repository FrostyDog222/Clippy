"""Twitch helpers that need no account and no API key.

top_streams(): Twitch's public web GQL endpoint with the public web Client-Id (same one yt-dlp uses).
irc_chat():    anonymous IRC read-only chat (justinfan nick, no password).
vod_chat():    not implemented, see note at the bottom.
"""
import json, random, socket, ssl, sys, threading, time
from datetime import datetime, timezone

GQL = "https://gql.twitch.tv/gql"
CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"

try:
    import truststore
    truststore.inject_into_ssl()
except Exception:
    pass


def gql(query, variables=None):
    import requests
    r = requests.post(GQL, json={"query": query, "variables": variables or {}},
                      headers={"Client-Id": CLIENT_ID}, timeout=15)
    r.raise_for_status()
    return r.json()


_Q_GAME = """query($name:String!,$n:Int!){ game(name:$name){ displayName streams(first:$n){ edges{ node{
 title viewersCount createdAt previewImageURL(width:640,height:360) broadcaster{ login displayName broadcastSettings{ language } } } } } } }"""
_Q_SEARCH = """query($q:String!){ searchCategories(query:$q,first:5){ edges{ node{ ... on Game{ name displayName } } } } }"""


def _rows(game):
    out = []
    for e in ((game.get("streams") or {}).get("edges") or []):
        n = e.get("node") or {}
        b = n.get("broadcaster") or {}
        if not b.get("login"):
            continue
        out.append({"login": b["login"], "name": b.get("displayName") or b["login"], "title": n.get("title") or "",
                    "viewers": n.get("viewersCount") or 0, "created_at": n.get("createdAt"),
                    "preview": n.get("previewImageURL"), "url": f"https://twitch.tv/{b['login']}",
                    "lang": str((b.get("broadcastSettings") or {}).get("language") or "").lower()})  # the language the streamer set (en, pt, de, es...)
    return out


_Q_CLIPS = """query($name:String!,$n:Int!,$p:ClipsPeriod){ game(name:$name){ displayName clips(first:$n, criteria:{period:$p, sort:VIEWS_DESC, languages:[EN]}){ edges{ node{
 slug title viewCount durationSeconds createdAt url broadcaster{ displayName } } } } } }"""
_Q_TOPGAMES = """query($n:Int!){ games(first:$n){ edges{ node{ displayName } } } }"""


def top_clips(game=None, limit=10, period="LAST_DAY"):
    """The most viewed clips viewers made on Twitch (no key). game=None: the clips of the 3 most watched categories right now. Rows: id, url, title, channel,
    duration, views. A clip is a moment the viewers already picked, so it is a strong trending signal. Empty list on any problem."""
    out = []
    try:
        names = [game] if game else [e["node"]["displayName"] for e in (gql(_Q_TOPGAMES, {"n": 3}).get("data") or {}).get("games", {}).get("edges", [])]
        for name in names:
            try:  # the user may type a game loosely ("elden ring"): resolve it to the exact Twitch category name
                found = [e["node"] for e in (gql(_Q_SEARCH, {"q": name}).get("data") or {}).get("searchCategories", {}).get("edges", []) if e.get("node")]
                name = (found[0].get("displayName") or name) if found else name
            except Exception:
                pass
            edges = []
            for per in dict.fromkeys((period, "LAST_WEEK")):  # a quiet topic has no clips today: look at the last week
                g = (gql(_Q_CLIPS, {"name": name, "n": limit, "p": per}).get("data") or {}).get("game") or {}
                edges = (g.get("clips") or {}).get("edges", [])
                if edges:
                    break
            for e in edges:
                n = e["node"]
                out.append({"id": "tw:" + n["slug"], "url": n["url"], "title": n["title"] or "", "channel": (n.get("broadcaster") or {}).get("displayName") or "?",
                            "duration": n.get("durationSeconds") or 0, "views": n.get("viewCount") or 0, "game": g.get("displayName") or name})
    except Exception as e:
        print(f"twitch clips failed: {e}", file=sys.stderr)
    return sorted(out, key=lambda x: -x["views"])


_Q_USERCLIPS = """query($login:String!,$n:Int!,$p:ClipsPeriod){ user(login:$login){ displayName clips(first:$n, criteria:{period:$p, sort:VIEWS_DESC}){ edges{ node{
 slug title viewCount durationSeconds createdAt url broadcaster{ displayName } game{ displayName } } } } } }"""


def channel_clips(name, limit=15, period="LAST_WEEK"):
    """The most viewed clips viewers made on ONE streamer's channel (same free web endpoint as top_clips). The channel login is guessed from the name
    ('CaseOh' -> caseoh, then caseoh_); rows like top_clips plus the game. Empty list when the channel is not found or on any problem."""
    import re
    base = re.sub(r"[^a-z0-9_]", "", str(name).lower())
    for login in dict.fromkeys((base, base + "_")):
        if not login:
            continue
        try:
            u = (gql(_Q_USERCLIPS, {"login": login, "n": limit, "p": period}).get("data") or {}).get("user")
        except Exception as e:
            print(f"twitch channel clips failed: {e}", file=sys.stderr); continue
        edges = ((u or {}).get("clips") or {}).get("edges") or []
        if edges:
            out = [{"id": "tw:" + n["slug"], "url": n["url"], "title": n.get("title") or "", "channel": (n.get("broadcaster") or {}).get("displayName") or name,
                    "duration": n.get("durationSeconds") or 0, "views": n.get("viewCount") or 0, "game": (n.get("game") or {}).get("displayName") or ""}
                   for n in (e["node"] for e in edges)]
            return sorted(out, key=lambda x: -x["views"])
    return []


def top_streams(game="Grand Theft Auto VI", limit=20):
    """Live streams of a Twitch category, sorted by viewers (desc). Empty list if the category is unknown/offline."""
    names = [game]
    try:
        d = gql(_Q_SEARCH, {"q": game})
        for e in ((d.get("data") or {}).get("searchCategories") or {}).get("edges") or []:
            nm = (e.get("node") or {}).get("name")
            if nm and nm not in names:
                names.append(nm)
    except Exception as ex:
        print(f"twitch category search failed: {ex}", file=sys.stderr)
    if game.lower() != "gta vi":
        names.append("Grand Theft Auto VI" if "grand theft" not in game.lower() else "GTA VI")
    for nm in names:
        try:
            g = ((gql(_Q_GAME, {"name": nm, "n": int(limit)}).get("data") or {}).get("game"))
        except Exception as ex:
            print(f"twitch gql failed: {ex}", file=sys.stderr)
            continue
        if g:
            rows = _rows(g)
            if rows:
                return sorted(rows, key=lambda r: r["viewers"], reverse=True)
    return []


def started_ts(iso):
    try:
        return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except Exception:
        return None


def _parse_privmsg(line):
    """'@tags :user!user@user.tmi.twitch.tv PRIVMSG #chan :text' -> (user, text) or None"""
    tags = ""
    if line.startswith("@"):
        tags, _, line = line.partition(" ")
    if " PRIVMSG " not in line:
        return None
    prefix, _, rest = line.partition(" PRIVMSG ")
    user = prefix.lstrip(":").split("!")[0]
    for t in tags.lstrip("@").split(";"):
        if t.startswith("display-name=") and t[13:]:
            user = t[13:]
    _, _, text = rest.partition(" :")
    return user, text.strip()


def irc_chat(channel, out_path, stop_event=None, host="irc.chat.twitch.tv", port=6697):
    """Append {t, user, text} JSON lines to out_path until stop_event is set. Reconnects on drop.
    Uses TLS (6697); falls back to plain 6667 if TLS fails."""
    stop_event = stop_event or threading.Event()
    channel = channel.lower().lstrip("#")
    use_tls = True
    with open(out_path, "a", encoding="utf-8", buffering=1) as out:
        while not stop_event.is_set():
            sock = None
            try:
                raw = socket.create_connection((host, port if use_tls else 6667), timeout=15)
                sock = ssl.create_default_context().wrap_socket(raw, server_hostname=host) if use_tls else raw
                sock.settimeout(5)
                sock.sendall(f"NICK justinfan{random.randint(10000, 99999)}\r\n".encode())
                sock.sendall(f"JOIN #{channel}\r\n".encode())
                buf = b""
                while not stop_event.is_set():
                    try:
                        data = sock.recv(4096)
                    except socket.timeout:
                        continue
                    if not data:
                        break
                    buf += data
                    *lines, buf = buf.split(b"\r\n")
                    for ln in lines:
                        s = ln.decode("utf-8", "replace")
                        if s.startswith("PING"):
                            sock.sendall(("PONG" + s[4:] + "\r\n").encode())
                            continue
                        m = _parse_privmsg(s)
                        if m and m[1]:
                            out.write(json.dumps({"t": time.time(), "user": m[0], "text": m[1]}, ensure_ascii=False) + "\n")
            except (OSError, ssl.SSLError) as ex:
                if use_tls and not isinstance(ex, socket.timeout):
                    use_tls = False  # maybe TLS is blocked; try plain 6667 next
            finally:
                if sock:
                    try: sock.close()
                    except OSError: pass
            stop_event.wait(3)


def vod_chat(video_id):
    """Not implemented: VideoCommentsByOffsetOrCursor is only served through a persisted-query sha256 hash
    that cannot be verified offline here, and plain-text queries for it are rejected."""
    raise NotImplementedError("Twitch VOD chat needs a persisted-query hash; not supported")
