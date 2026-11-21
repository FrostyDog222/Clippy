"""'Check before posting' flag for a finished clip (a hint only: it never blocks or hides a clip). Looks at the clip's text (the on-video line, the source video's title, the
joke) for adult words. Picture-based nudity detection was tried (skin-colour test) and dropped: close-up faces and beige buildings trigger it far more than real nudity,
and a wrong warning teaches you to ignore the right ones. flags(d) -> list of plain-language strings (empty = nothing to check)."""
import re

ADULT = re.compile(r"\b(nude|nudes|naked|nudity|topless|strip ?club|stripper|onlyfans|porn\w*|sex|sexy|boobs?|tits|nsfw|18\+)\b", re.I)


def flags(d):
    text = " ".join(str(d.get(k) or "") for k in ("hook", "title", "blurb", "source_title"))
    return ["adult words in the text or the source title (check the picture)"] if ADULT.search(text) else []
