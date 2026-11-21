"""Language helpers for clips (pure logic, no network, no models). Used by moments.py (is this moment in a language you allow?) and cut.py (captions: yes / no, which font).

The idea, in plain words:
 * The speech model says which language it heard and how sure it is. At TRUST (80%) or more Clippy believes it. Below that Clippy is unsure.
 * Streamers mix languages ("what the fuck" in the middle of their own language). So a moment is judged by the words in it: it counts as "in an allowed language" when
   at least ALLOWED_SHARE (25%) of its words are in a language you allowed, wherever they come from.
 * Unsure + the words look English -> English captions as always. Unsure + not English-looking -> no captions (and a "language unsure" flag), never a guess.
English clips (English heard with 80%+) are decided exactly as before: captions on, allowed.
"""
import functools, re

TRUST = 0.8
ALLOWED_SHARE = 0.25
MIN_WORDS = 4       # fewer words than this say nothing about the language

# script -> the language of the list a script points to (Settings language codes)
SCRIPT_LANG = {"cyrillic": "ru", "arabic": "ar", "devanagari": "hi", "hangul": "ko", "kana": "ja", "han": "ja"}
_RANGES = [("cyrillic", 0x0400, 0x052F), ("arabic", 0x0600, 0x06FF), ("arabic", 0x0750, 0x077F), ("arabic", 0xFB50, 0xFDFF), ("arabic", 0xFE70, 0xFEFF), ("devanagari", 0x0900, 0x097F),
           ("hangul", 0x1100, 0x11FF), ("hangul", 0x3130, 0x318F), ("hangul", 0xAC00, 0xD7AF), ("kana", 0x3040, 0x30FF), ("han", 0x4E00, 0x9FFF), ("han", 0x3400, 0x4DBF)]

# Clearly English words only (nothing that is also a common word of another Latin-script language: no "a", "no", "me", "he", "in", "on", "was", "die", "man", "to", "do").
ENGLISH = set("""
the and but or because so if then than that this these those there here what when where who whom whose why how which while with without within about above after again against all
also always am are aren't around as at away back been before being below between both by can can't cannot could couldn't did didn't do does doesn't doing don't down during each even ever every
few for from further get gets getting got had hadn't has hasn't have haven't having her hers herself him himself his how i i'd i'll i'm i've into is isn't it it's its itself just let's like
made make many may might more most much must my myself never next nobody none nor not nothing now of off often oh ok okay once one only other our ours out over own really right same say said
shall she should shouldn't since some something somebody someone still such take than their theirs them themselves they they'd they'll they're they've think thing things though through
thought till too try under until up upon us use very want wanted wants was wasn't way we we'd we'll we're we've well were weren't what's whatever when's where's who's will won't would wouldn't
yeah yep nope yes you you'd you'll you're you've your yours yourself yourselves
go going gone goes went come comes coming came look looks looking looked see saw seen know knew known need needs needed give gave given tell told talk talking play playing played run running ran
stop stopped start started wait waiting watch watching listen hear heard feel felt find found keep kept leave left mean means meant call called ask asked put show showed turn turned move moved
good great bad best worse worst nice cool sick insane crazy wild nuts mad sad happy funny dead alive big small little long short high low fast slow hard easy old new first last
guys guy dude bro bruh man's mate chat stream streamer viewers game games gameplay level mission missions car cars truck bike plane helicopter boat gun guns weapon cop cops police wanted stars
money cash dollars bucks bank robbery heist crash crashed explosion bridge road street city beach swamp gator alligator shark snake
fuck fucking fucked fuck's shit shitty damn dammit hell holy god gosh jesus christ crap ass asshole bitch bastard wtf omg lmao lol rofl kek pog poggers clip clips banger goat insane
no-way nooo noooo nooooo please sorry thanks thank welcome hello hey hi bye goodbye today tonight tomorrow yesterday time times day days night week year years minute minutes second seconds
people person everybody everyone everything anything anyone anywhere somewhere nowhere maybe probably actually literally basically honestly seriously totally definitely exactly absolutely
three four five six seven eight nine ten twenty thirty hundred thousand million
""".split())

_LETTER = re.compile(r"[^\W\d_]", re.UNICODE)


def script_of(word):
    """The script of the first letter of a word: 'cyrillic', 'arabic', 'devanagari', 'hangul', 'kana', 'han', 'latin', or None when there are no letters."""
    m = _LETTER.search(word or "")
    if not m:
        return None
    o = ord(m.group(0))
    for name, a, b in _RANGES:
        if a <= o <= b:
            return name
    return "latin" if o < 0x250 or 0x1E00 <= o <= 0x1EFF else "other"


def tag(word):
    """'en' for a clearly English word, the script name for a non-Latin word, 'latin' for any other Latin-script word, None for numbers and punctuation."""
    s = script_of(word)
    if s != "latin":
        return s
    w = re.sub(r"[^\w']", "", (word or "").lower()).strip("'")
    return "en" if w in ENGLISH else "latin"


def _tokens(words):
    return [t for t in (tag(w if isinstance(w, str) else w.get("w", "")) for w in words) if t]


def english_share(words):
    """Share of the Latin-script words that are clearly English (0..1); 0 when there are none."""
    t = [x for x in _tokens(words) if x in ("en", "latin")]
    return (t.count("en") / len(t)) if t else 0.0


def language_share(words, allowed, main=None, main_p=0.0):
    """Share (0..1) of a moment's words that are in a language the user allowed. English words count when English is allowed; non-Latin words count for the language of their
    script; other Latin-script words count only when the main language was heard with TRUST confidence and is allowed. allowed None = everything is allowed."""
    if allowed is None:
        return 1.0
    allowed = [a.lower() for a in allowed]
    toks = _tokens(words)
    if not toks:
        return 0.0
    main_ok = bool(main) and main_p >= TRUST and main.lower() in allowed
    n = 0
    for t in toks:
        if t == "en":
            n += "en" in allowed
        elif t == "latin":
            n += main_ok
        elif t in SCRIPT_LANG:
            n += SCRIPT_LANG[t] in allowed
    return n / len(toks)


def trusted(info):
    return float((info or {}).get("p") or 0) >= TRUST


def moment_allowed(info, words, allowed):
    """Is this moment in a language the user allows? Rejected only when the speech model is SURE (80%+) of a language that is not allowed AND fewer than ALLOWED_SHARE of the
    moment's words are in an allowed language (so a burst of English inside another language still counts). An unsure language never rejects a moment."""
    if allowed is None or not info:
        return True
    lang = str(info.get("language") or "").lower()
    if not trusted(info) or lang in [a.lower() for a in allowed]:
        return True
    return language_share(words, allowed, lang, float(info.get("p") or 0)) >= ALLOWED_SHARE


def caption_plan(info, words):
    """Captions for this clip? -> {"captions": bool, "unsure": bool, "reason": str}. Sure (80%+): yes, as always. Unsure: yes when the words look English, otherwise no
    captions and unsure=True (the dashboard shows a 'language unsure' flag). With almost no words there is nothing to get wrong: yes."""
    if trusted(info) or not info:
        return {"captions": True, "unsure": False, "reason": "language trusted"}
    toks = [t for t in _tokens(words) if t]
    if len(toks) < MIN_WORDS:
        return {"captions": True, "unsure": False, "reason": "too few words to doubt"}
    if english_share(words) >= 0.6 and all(t in ("en", "latin") for t in toks):
        return {"captions": True, "unsure": False, "reason": "unsure language but the words look English"}
    return {"captions": False, "unsure": True, "reason": f"language unsure ({info.get('language')} {info.get('p')}) and the words do not look English"}


FONT_FOR_SCRIPT = {"arabic": ("Segoe UI", ["segoeuib.ttf"]), "hangul": ("Malgun Gothic", ["malgunbd.ttf"]), "kana": ("Yu Gothic", ["YuGothB.ttc"]),
                   "han": ("Microsoft YaHei", ["msyhbd.ttc"]), "devanagari": ("Nirmala UI", ["Nirmala.ttc"])}   # Latin and Cyrillic keep Arial Black


@functools.lru_cache(maxsize=64)
def font_for_script(script, fonts_dir=r"C:\Windows\Fonts"):
    """The caption font for a script that Arial Black cannot draw (a Windows font that is installed), or None to keep the normal font."""
    import os
    f = FONT_FOR_SCRIPT.get(script)
    if f and any(os.path.exists(os.path.join(fonts_dir, x)) for x in f[1]):
        return f[0]
    return None


def upper(text, lang=None):
    """Caption capitals: Turkish has a dotted I (i -> İ, ı -> I); scripts without capitals are left alone by str.upper()."""
    if (lang or "").lower() == "tr":
        text = text.replace("i", "İ").replace("ı", "I")
    return text.upper()


# Reaction words per language (shock/hype, laughter, mild swearing). Used ONLY for clips whose language was heard with TRUST confidence and is not English; English keeps its own lists.
LEX = {
    "es": ("no puede ser|dios mio|madre mia|joder|increible|vamos|que locura|no me lo creo|de locos", "jaja|jeje|jajaja|jajajaja", "joder|mierda|cabron|coño|puta|hostia"),
    "pt": ("meu deus|nossa|caramba|que isso|inacreditavel|vamos|não acredito|não pode ser", "kkk|kkkk|rsrs|haha|hahaha", "porra|caralho|merda|puta|foda"),
    "fr": ("oh mon dieu|putain|incroyable|c'est fou|allez|non mais|sérieux|t'es sérieux", "mdr|ptdr|haha|hahaha", "putain|merde|bordel|con|chier"),
    "de": ("oh mein gott|krass|alter|wahnsinn|unglaublich|echt jetzt|digga|nee|geil", "haha|hahaha|ahaha", "scheiße|scheisse|verdammt|arsch|fick|kacke"),
    "it": ("mamma mia|oddio|incredibile|che roba|dai|non ci credo|assurdo|madonna", "haha|ahah|ahahah", "cazzo|merda|porca|stronzo|vaffanculo"),
    "ru": ("боже мой|ничего себе|офигеть|капец|нифига|не может быть|вау|ого|жесть|пиздец", "хаха|ахаха|хах|ха-ха", "блин|блять|бля|сука|хрен|черт"),
    "tr": ("yok artık|inanılmaz|ya|aman allahım|vay be|olamaz|çıldırdım|bu ne", "hahaha|haha|ahaha", "lan|siktir|amına|orospu|bok"),
    "ar": ("يا الله|ما هذا|لا يمكن|مستحيل|والله|يا رجل|ياساتر|كيف", "هههه|ههه|هاها", "تبا|اللعنة|زفت"),
    "ja": ("やばい|すごい|まじ|うそ|嘘でしょ|えっ|ええ|信じられない", "w+|笑|ははは|あはは", "くそ|クソ|ちくしょう"),
    "ko": ("대박|와|헐|진짜|말도 안 돼|미쳤다|우와|어머", "ㅋㅋ+|ㅎㅎ+|하하", "씨발|젠장|개새|좆"),
    "ro": ("nu se poate|doamne|incredibil|haide|ce naiba|pe bune|mama mia|nebunie", "haha|hahaha|ahaha|hihi", "dracu|naiba|pula|fut|căcat|rahat"),
    "pl": ("o boże|niemożliwe|nie wierzę|szok|ale jazda|no nie|co jest", "haha|hahaha|xd", "kurwa|cholera|do cholery|szlag|dupa"),
    "nl": ("oh mijn god|ongelooflijk|echt waar|wauw|gekkenhuis|serieus", "haha|hahaha|lol", "kut|shit|verdomme|godver|klote"),
    "hi": ("हे भगवान|अरे यार|क्या बात|पागल|वाह|ये क्या|यार", "हाहा|हा हा|हाहाहा", "साला|कमीना|बकवास"),
    "id": ("astaga|gila|serius|wah|ya ampun|mantap|nggak mungkin", "wkwk|wkwkwk|haha|hahaha", "anjing|sialan|bangsat|tai"),
}
_LEXRE = {}


def _compile(lang):
    if lang not in _LEXRE:
        h, l, s = LEX[lang]
        _LEXRE[lang] = tuple(re.compile(r"(?:^|\W)(?:%s)(?:\W|$)" % p, re.I) for p in (h, l, s))
    return _LEXRE[lang]


def reaction(text, info):
    """-> ("hype"|"laugh"|"swear"|None). Only for a trusted, non-English language that has a list; English words are handled by the normal lists."""
    lang = str((info or {}).get("language") or "").lower()
    if lang == "en" or not trusted(info) or lang not in LEX:
        return None
    h, l, s = _compile(lang)
    return "laugh" if l.search(text) else "swear" if s.search(text) else "hype" if h.search(text) else None
