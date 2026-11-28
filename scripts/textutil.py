"""Small text helpers shared by autorun.py and make_meta.py (no network, no AI)."""
import json, pathlib, re

SWEAR = re.compile(r"(fuck\w*|shit\w*|bitch\w*|nigg\w*|cunt\w*|dick\w*|pussy\w*|asshole\w*|whore\w*|slut\w*"
                   r"|\bpul[aăeiu]\b|\bpizd\w*|\bmui[ei]\w*|\bf[uû]t(u|e|ut|ui|ere|eți|ând)\b|\bc[aă]c[aă][tț]\w*|\brahat\w*|\bcurv\w*|\bdracu\w*|\bpoponar\w*|\bjidan\w*|\bț[iî]g[aă]n\w*|\bhandicapat\w*|\bretardat\w*)", re.I)  # + Romanian
BANNED = re.compile(r"\b(leak|leaked|early access|play early|free download|download free|crack|piracy|cheat engine|giveaway)\b", re.I)
REACT = re.compile(r"(what is this|no way|oh my (god|gosh)|are you serious|insane|crazy|let'?s go|holy|can you do this|i can'?t believe|look at (this|that)|\?)", re.I)


def mask(s):
    """f*** style masking, same idea as the caption masking in cut.py."""
    return SWEAR.sub(lambda m: m.group(0)[0] + "*" * (len(m.group(0)) - 1), s)


def sentences(text):
    return [x.strip(" .,-") for x in re.split(r"(?<=[.!?])\s+", text) if x.strip(" .,-")]


def cap(s):
    return s[0].upper() + s[1:] if s else s


def quote_hook(text):
    """A short real quote from the clip (3-9 words, no swearing, no banned words), preferring reaction phrases. None if nothing fits."""
    best = None
    for s in sentences(text):
        if 3 <= len(s.split()) <= 9 and not SWEAR.search(s) and not BANNED.search(s):
            if REACT.search(s):
                return cap(s)
            best = best or cap(s)
    return best


def shorten(s, n=14):
    """Whisper output has little punctuation, so a 'sentence' can be a long run-on: keep the last n words (reaction usually comes last)."""
    w = s.split()
    return s if len(w) <= n else "..." + " ".join(w[-n:])


def excerpt(text, limit=200):
    """What is really said in the clip (swears masked): reaction lines first, each trimmed, up to ~limit chars."""
    ss = [s for s in sentences(text) if len(s.split()) >= 3 and not BANNED.search(s)]
    ordered = [s for s in ss if REACT.search(s)] + [s for s in ss if not REACT.search(s)]
    out, used = [], 0
    for s in ordered:
        s = cap(mask(shorten(s)))
        if used + len(s) > limit and out:
            break
        out.append(s); used += len(s) + 2
        if len(out) == 3:
            break
    return " / ".join(out)


def clip_text(clip_json_path):
    """Words spoken in the clip, from the job's transcript.json (clip json holds start/end in source seconds)."""
    c = json.loads(pathlib.Path(clip_json_path).read_text(encoding="utf-8"))
    job = pathlib.Path(c["file"]).parent.parent
    words = json.loads((job / "transcript.json").read_text(encoding="utf-8"))
    return " ".join(w["w"] for w in words if c["start"] <= w["s"] < c["end"])


LANGS = {"en": "English", "es": "Spanish", "pt": "Portuguese", "fr": "French", "de": "German", "it": "Italian", "ru": "Russian", "tr": "Turkish",
         "ar": "Arabic", "ja": "Japanese", "ko": "Korean", "ro": "Romanian", "pl": "Polish", "nl": "Dutch", "hi": "Hindi", "id": "Indonesian"}


def allowed_languages(cfg):
    """Config "languages": list of codes the user picked. None = any language (empty list, "any", or missing). Old value "en" still works."""
    v = cfg.get("languages", ["en"])
    if v in ("any", None, [], ""):
        return None
    return [v] if isinstance(v, str) else [str(x) for x in v]


# chat shock/hype reactions (strong) and mild laughter; "WTF"-type messages are what a real moment looks like in chat
CHAT_SHOCK = re.compile(r"(\bWT[FH]\b|\bWHAT THE\b|\bOMG\b|\bOMFG\b|\bNO WAY\b|\bHOLY\b|\bBRO+\b|\bBRUH\b|\bI CANT\b|\bICANT\b|\bCLIP( IT)?\b|\bPOG(GERS|CHAMP)?\b|\bINSANE\b|\bCRAZY\b|\bDEAD\b|\bNOO+\b|\bWHAT\?+|\?!|!\?|💀|😭|🤯|😱|🔥|😮|🙀|\bWO+W+\b|\bPOGU\b|\bMONKA[SW]\b|\bB[ĂA]I\b|\bMAM[ĂA]\b|\bDOAMNE\b|\bNU SE POATE\b|\bINCREDIBIL\b|\bNEBUNIE\b|\bVAI DE MINE\b|\bNO PUEDE SER\b|\bMADRE M[IÍ]A\b|\bDIOS M[IÍ]O\b|\bJODER\b|\bQU[EÉ] LOCURA\b|\bMEU DEUS\b|\bMDS\b|\bCARAMBA\b|\bCARAC[AÁ]\b|\bN[AÃ]O ACREDITO\b|\bEITA\b|\bKRASS\b|\bDIGGA\b|\bWAHNSINN\b|\bOHA\b|\bPUTAIN\b|\bINCROYABLE\b|\bMON DIEU\b)", re.I)  # + shock in other languages
CHAT_LAUGH = re.compile(r"(\bLMAO+\b|\bLOL\b|\bKEKW?\b|\bOMEGALUL\b|\bLUL\b|\bAHAHA|😂|🤣|😆|😹|\bja(ja)+j?\b|\bje(je)+\b|\bk{3,}\b|\brs(rs)+\b|\bha(ha)+h?\b|\bhe(he)+\b|\bp?t?mdr\b|\bx+d{2,}\b|\bLULW\b)", re.I)  # + laughter in Spanish, Portuguese, French, German...


# "in <language>" in that language: added to YouTube searches so the results come from creators of the country that speaks it (the speech check afterwards still has the last word)
LANG_HINT = {"es": "en español", "pt": "em português", "fr": "en français", "de": "auf deutsch", "it": "in italiano", "ru": "на русском", "tr": "türkçe", "ar": "بالعربي",
             "ja": "日本語", "ko": "한국어", "ro": "în română", "pl": "po polsku", "nl": "in het nederlands", "hi": "हिंदी में", "id": "bahasa indonesia"}


def search_queries(queries, cfg):
    """The YouTube searches for the languages picked in Settings. English only / any language: unchanged. Otherwise each language gets its own searches
    ("gta 6 reaction en español"), and English ones stay only when English is picked too."""
    langs = allowed_languages(cfg)
    if not langs or langs == ["en"]:
        return list(queries)
    out = list(queries) if "en" in langs else []
    for code in langs:
        if code in LANG_HINT:
            out += [f"{q} {LANG_HINT[code]}" for q in list(queries)[:3]]
    return out or list(queries)


def twitch_languages(cfg):
    """Twitch clip language codes (uppercase: EN, ES, RO...) for the picked languages; None = any language."""
    langs = allowed_languages(cfg)
    return [c.upper() for c in langs if c in LANGS] if langs else None


def stream_language_ok(lang, cfg):
    """A live stream's language (en, pt, de... as the streamer set it on Twitch; '' = unknown) against the languages you picked in Settings: unknown is allowed."""
    allowed = allowed_languages(cfg)
    return not lang or allowed is None or str(lang).lower() in [a.lower() for a in allowed]


def chat_weight(txt):
    """Per-message weight: shock reactions (WTF, OMG, NO WAY...) count most, laughter some, plain messages 1."""
    return 3 if CHAT_SHOCK.search(txt) else 2 if CHAT_LAUGH.search(txt) else 1


# ---- no-AI titles: detect what the moment is about from the words (speech + chat) and pick a short, clean line from a bank ----
EVENTS = [  # (name, regex, on-video titles, YouTube titles, one-liners)
    ("air", r"\b(plane|planes|jet|jets|helicopter|chopper|airplane|pilot|landing|fly|flying|flew)\b",
     ["PLANE PROBLEMS", "NOT THE LANDING", "THIS FLIGHT WENT WRONG"], ["Flying in GTA 6 went wrong fast ✈️", "This landing did not go to plan ✈️"],
     ["Pilot of the year, clearly ✈️", "That flight escalated quickly 💀"]),
    ("crash", r"\b(crash|crashed|crashing|wreck|wrecked|flip|flipped|explod\w*|explosion|boom)\b",
     ["THAT HIT HARD", "TOTALLED", "WELL, THAT HAPPENED"], ["The crash nobody saw coming 💥", "That went off with a bang 💥"],
     ["He did not stop in time 💀", "Insurance will not cover that 💥"]),
    ("police", r"\b(cops?|police|wanted|stars?|chase|chased|busted|arrest\w*)\b",
     ["FIVE STARS ALREADY", "THE COPS ARE ANGRY", "NO WAY OUT"], ["The cops did not hold back 🚓", "A police chase that got out of hand 🚓"],
     ["Wanted level went up fast 🚓", "He really thought he could lose them 💀"]),
    ("fight", r"\b(gun|guns|shoot\w*|shot|kill\w*|dead|die|died|fight\w*|rob\w*|heist|weapon|rpg|ambush\w*)\b",
     ["IT ESCALATED FAST", "NOT GOING DOWN WITHOUT A FIGHT", "THIS GOT OUT OF HAND"], ["This fight got out of hand fast 🔫", "Nobody expected this to turn violent 🔫"],
     ["It was calm for about two seconds 💀", "He picked the wrong fight 🔫"]),
    ("gator", r"\b(gator|gators|alligator|alligators|swamp|snake|shark)\b",
     ["THE GATORS ARE REAL", "NOT THE SWAMP"], ["The wildlife in GTA 6 is not friendly 🐊", "Do not go near the swamp 🐊"],
     ["Nature said no 🐊", "The swamp has opinions 💀"]),
    ("car", r"\b(car|cars|driving|drive|driver|truck|bike|motorcycle|speed|speeding|road)\b",
     ["DRIVING LESSONS", "WHO GAVE HIM A LICENSE", "THAT TURN THOUGH"], ["The driving physics in GTA 6 are wild 🚗", "This drive did not go as planned 🚗"],
     ["Zero brakes, full confidence 🚗", "Driving test: failed 💀"]),
]
GENERIC = (["THIS GOT WILD", "NOBODY SAW THAT COMING", "GTA 6 IS INSANE"], ["GTA 6 gameplay that got out of hand 🔥", "A moment in GTA 6 you have to see 🔥"],
           ["Not what he expected 💀", "GTA 6 never misses 🔥"])


def noai_titles(text, chat=None, creator=""):
    """{"on_video", "youtube_title", "tiktok_line", "event"} from the clip words (+ what chat said, live mode). No AI, no network; lines come from fixed
    clean banks, picked by a hash of the text so different clips vary. Returns event None when nothing specific was recognised (generic lines)."""
    import hashlib
    low = (text or "").lower()
    seed = int(hashlib.sha1(low.encode("utf-8", "ignore")).hexdigest()[:8], 16)
    for name, rx, ov, yt, line in EVENTS:
        if re.search(rx, low):
            break
    else:
        name, (ov, yt, line) = None, GENERIC
    pick = lambda xs, k=0: xs[(seed >> k) % len(xs)]
    d = {"event": name, "on_video": pick(ov), "youtube_title": pick(yt, 3), "tiktok_line": pick(line, 6)}
    reacts = [m for m in (chat or []) if chat_weight(m) >= 2]
    if len(reacts) >= 6 and name is None:  # live: nothing specific in the words but chat clearly lost it
        d["on_video"] = "CHAT LOST IT"; d["tiktok_line"] = "Chat completely lost it 💀"
    return d
