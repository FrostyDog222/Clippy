"""Find the best clip moments in a video or VOD.

Signals per second: loudness spikes, YouTube "most replayed" heatmap, live-chat spikes
(YouTube VOD chat replay or a TwitchDownloader chat.json) and hype words in the transcript.
Writes jobs/<id>/candidates.md + candidates.json.

usage: python moments.py <url-or-file> [--top 8] [--min 15] [--max 40] [--chat chat.json] [--max-minutes 90]
"""
import argparse, json, math, re, subprocess, sys, wave
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).parent))
from common import ffprobe, is_url, job_dir, load_json, run, save_json, tool, ytdlp_base, ytdlp_json

TALK = re.compile(r"\b(chat|y\s?'?all|subs?|subscribe|donations?|gifted|should i|my channel)\b", re.I)
# words that show the streamer is really in the game around this moment (a 700-minute stream is mostly talking / other games)
try:
    import topic as _topic
    GTA_TOPIC = _topic.info()["gta"]
except Exception:
    GTA_TOPIC = True
GAME = re.compile(r"\b(gta|vice city|leonida|lucia|jason|mission|cops?|police|wanted|heist|cars?|truck|bike|motorcycle|helicopter|chopper|plane|jet|boat|gun|guns|shoot\w*|shot|crash\w*|drive|driving|driver|steal|stole|rob\w*|bank|store|gang|dealer|club|beach|gator|alligator|swamp|miami|wasted|busted|arrest\w*|explo\w*|rpg|tank|parachute|skydiv\w*|character|map|rockstar|graphics|npc|pedestrian|run|chase|fight|kill\w*|dead|die|died|boss|loot|weapon)\b", re.I)
PROMO = re.compile(r"\b(giveaways?|raffle|promo code|use code|sponsor(ed)?|drops? (are|is) (on|enabled)|type in chat|enter (the|my) (giveaway|raffle|contest|draw)|link in)\b", re.I)
PRE = 3.0  # seconds of lead-in before the event
STRONG = 4.0  # viral score a moment needs to be worth posting (voice, laughter, swearing, action, loudness, chat...); calibrated so mission chatter and cutscene dialogue stay under it. autorun's strict mode wants 5.0
# strong reaction words only: common filler ("what", "wait", "bro", "yo", "nah", "run") also appears constantly in game cutscene dialogue and menus
HYPE = re.compile(r"\b(no way|oh my god|oh my gosh|omg|let'?s go+|holy|insane|crazy|wtf|look at (this|that)|are you serious|what the (hell|fuck)|bro what|go go go)\b", re.I)
import pick_rules as PR  # noqa: E402  (polish 1: capped z-scores, talk rule; the old logic is in backup_logic_2026-11-22)
from textutil import SWEAR, chat_weight  # noqa: E402  (chat shock/laughter word weights, shared with live.py)


def fetch(src, d, max_min, start_min=0):
    info = {}
    if is_url(src):
        info = ytdlp_json(src)
        save_json(d / "info.json", {k: info.get(k) for k in ("id", "title", "uploader", "channel", "uploader_id", "webpage_url",
                                                             "duration", "view_count", "upload_date", "heatmap", "is_live", "was_live")})
        vid = d / "source.mp4"
        if not vid.exists():
            args = ytdlp_base(src) + ["-f", "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080]/b", "--merge-output-format", "mp4",
                                   "-o", str(vid)]
            if start_min or (info.get("duration") or 0) > max_min * 60:  # long VODs: only analyse a slice (--start minutes in, --max-minutes long)
                args += ["--download-sections", f"*{start_min * 60}-{(start_min + max_min) * 60}"]
            run(args + [src])
        if info.get("was_live") and not (d / "chat.json").exists():  # YouTube chat replay
            run(ytdlp_base(src) + ["--skip-download", "--write-subs", "--sub-langs", "live_chat", "-o", str(d / "chat"), src], check=False)
    else:
        vid = Path(src).resolve()
        save_json(d / "info.json", {"title": vid.stem, "webpage_url": str(vid)})
    return vid, info


def audio_rms(vid, d):
    wav = d / "audio.wav"
    if not wav.exists():
        run([tool("ffmpeg"), "-y", "-v", "error", "-i", str(vid), "-ac", "1", "-ar", "16000", str(wav)])
    with wave.open(str(wav)) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    n = len(x) // 16000
    rms = np.sqrt((x[: n * 16000].reshape(n, 16000) ** 2).mean(1) + 1e-9)
    return wav, 20 * np.log10(rms)


def _xreact(word, lang):
    try:
        import langkit
        return langkit.reaction(word, lang)
    except Exception:
        return None


LAUGH = re.compile(r"^(ha+h*|hah+a*|he+h+e*|lol|lmao|lmfao|kek\w*)[,.!?]*$", re.I)
NOVEL = re.compile(r"\b(glitch\w*|bug|bugged|physics|ragdoll\w*|launched|teleport\w*|stuck|impossible|never seen|how did|what happened|did you see|"
                   r"flying|floating|clipped|clipping|invincible|secret|easter egg|new)\b", re.I)


def voice_series(wav, n):
    """How excited the voice sounds, per second: pitch rising above the speaker's own usual pitch (semitones over the last minute) plus a brighter,
    harsher sound (shouting, screaming). 0 where nobody speaks. Pure numpy (autocorrelation pitch on the loudest 40 ms of each second)."""
    with wave.open(str(wav)) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    sr, win = 16000, 640
    hann = np.hanning(win).astype(np.float32); freqs = np.fft.rfftfreq(win, 1 / sr)
    f0 = np.full(n, np.nan); cen = np.full(n, np.nan)
    for s in range(n):
        seg = x[s * sr:(s + 1) * sr]
        if len(seg) < sr:
            break
        c = np.concatenate([[0], np.cumsum(seg ** 2)]); e = c[win:] - c[:-win]
        i = int(np.argmax(e))
        if e[i] / win < 1e-4:  # silence / far too quiet for speech
            continue
        fr = seg[i:i + win] * hann
        r = np.fft.irfft(np.abs(np.fft.rfft(fr, 2 * win)) ** 2)[:win]
        if r[0] <= 0:
            continue
        r = r / r[0]; lag = int(np.argmax(r[40:200])) + 40
        sp = np.abs(np.fft.rfft(fr))
        cen[s] = float((freqs * sp).sum() / (sp.sum() + 1e-9))
        if r[lag] > 0.35:
            f0[s] = sr / lag
    st = 12 * np.log2(np.where(np.isnan(f0), np.nan, f0) / 100)
    med, cmed = np.full(n, np.nan), np.full(n, np.nan)
    for s in range(n):
        a, b = max(0, s - 30), min(n, s + 31)
        if np.isfinite(st[a:b]).sum() >= 5:
            med[s] = np.nanmedian(st[a:b])
        if np.isfinite(cen[a:b]).sum() >= 5:
            cmed[s] = np.nanmedian(cen[a:b])
    up = np.nan_to_num(np.clip((st - med) / 4.0, 0, 3))
    cs = np.nanstd(cen) if np.isfinite(cen).any() else 1.0
    bright = np.nan_to_num(np.clip((cen - cmed) / (cs + 1e-9), 0, 3))
    return smooth(0.7 * up + 0.3 * bright, 3)


def motion_series(vid, d, n):
    """Per-second amount of on-screen change (4 fps grayscale 160x90, mean abs frame difference): high in action, low in menus/static shots."""
    f = d / "motion.npy"
    if f.exists():
        return np.load(f)[:n]
    w, h = 160, 90
    p = subprocess.run([tool("ffmpeg"), "-v", "error", "-i", str(vid), "-vf", f"fps=4,scale={w}:{h},format=gray", "-f", "rawvideo", "-"],
                       capture_output=True)
    fr = np.frombuffer(p.stdout, dtype=np.uint8)
    fr = fr[: len(fr) // (w * h) * (w * h)].reshape(-1, w * h).astype(np.float32)
    diff = np.abs(np.diff(fr, axis=0)).mean(1) if len(fr) > 1 else np.zeros(1)
    sec = np.array([diff[i * 4:(i + 1) * 4].mean() if len(diff[i * 4:(i + 1) * 4]) else 0 for i in range(n)])
    np.save(f, sec)
    return sec


def transcribe(wav, d):
    out = d / "transcript.json"
    if out.exists():
        return load_json(out)
    from faster_whisper import WhisperModel
    try:
        import ctranslate2
        cuda = ctranslate2.get_cuda_device_count() > 0
    except Exception:
        cuda = False
    with wave.open(str(wav)) as w:  # pass samples directly (16 kHz mono), no extra decoder needed
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768

    def run_on(device):
        model = WhisperModel("small", device=device, compute_type="float16" if device == "cuda" else "int8")
        segs, tinfo = model.transcribe(pcm, word_timestamps=True, vad_filter=True)
        save_json(d / "lang.json", {"language": tinfo.language, "p": round(float(tinfo.language_probability), 2)})  # English-only pipeline: see main()
        out_words = [{"w": w.word.strip(), "s": round(w.start, 2), "e": round(w.end, 2)} for s in segs for w in (s.words or [])]
        if tinfo.language != "en" or float(tinfo.language_probability) < 0.8:  # not clearly English: streamers switch languages mid-speech, so detect the language window by window
            try:
                segs2, _ = model.transcribe(pcm, word_timestamps=True, vad_filter=True, multilingual=True)
                w2 = [{"w": w.word.strip(), "s": round(w.start, 2), "e": round(w.end, 2)} for s in segs2 for w in (s.words or [])]
                if w2:
                    out_words = w2
                    save_json(d / "lang.json", {"language": tinfo.language, "p": round(float(tinfo.language_probability), 2), "multilingual": True})
            except Exception as e:
                print(f"multilingual pass skipped ({str(e)[:60]})")
        return out_words
    try:
        words = run_on("cuda" if cuda else "cpu")
    except RuntimeError as e:  # GPU present but CUDA libraries missing: fall back to CPU
        print(f"GPU transcription unavailable ({str(e)[:80]}), using CPU")
        words = run_on("cpu")
    save_json(out, words)
    return words


def _yt_texts(a):
    """Message texts inside one YouTube live-chat replay action (only the typed text, not author names)."""
    out = []
    for x in a.get("actions", []):
        r = (((x.get("addChatItemAction") or {}).get("item")) or {}).get("liveChatTextMessageRenderer")
        if r:
            out.append("".join(run.get("text") or (run.get("emoji") or {}).get("emojiId", "") for run in (r.get("message") or {}).get("runs", [])))
    return out


def chat_series(d, n, chat_arg, info, offset=0):
    """Per second: (all messages, reaction messages). A huge chat (30-60k viewers) always has many messages per second, so volume says
    little; what matters is how many of them are real reactions (WTF, OMG, NO WAY, 💀, LMAO...). Bot commands (!drops) are ignored."""
    tot, react = np.zeros(n), np.zeros(n)

    def add(t, txt):
        txt = (txt or "").strip()
        if not (0 <= t < n) or not txt or txt.startswith("!"):
            return
        tot[int(t)] += 1
        react[int(t)] += {3: 1.0, 2: 0.5}.get(chat_weight(txt), 0.0)

    yt = list(d.glob("chat*.live_chat.json"))
    if yt:
        for line in yt[0].read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                a = json.loads(line)["replayChatItemAction"]
                t = int(a["videoOffsetTimeMsec"]) / 1000 - offset  # offset = slice start (seconds into the full stream)
                texts = _yt_texts(a)
            except Exception:
                continue
            for txt in texts:
                add(t, txt)
    elif chat_arg:
        js = load_json(chat_arg)
        for m in js.get("comments", []):
            add(m.get("content_offset_seconds", 0) - offset, (m.get("message") or {}).get("body", ""))
    return tot, react


def laugh_audio(wav, n):
    """Laughter from the sound itself (the transcript almost never contains 'haha'): laughing is a burst train, about 4-5 bursts per second with deep
    on/off swings in loudness. Per second 0..2 = how strongly the last 1.5 s look like that. Pure numpy (loudness envelope every 50 ms, then its periodicity)."""
    with wave.open(str(wav)) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    hop, win = 800, 30
    m = len(x) // hop
    out = np.zeros(n)
    if m < win + 2:
        return out
    e = np.sqrt((x[:m * hop].reshape(m, hop) ** 2).mean(1)) + 1e-6
    le = np.log(e); le = le - smooth(le, 20)  # the swings only, not the slow level
    for i in range(0, m - win, 5):
        seg = le[i:i + win] - le[i:i + win].mean()
        var = float((seg ** 2).mean())
        if var < LAUGH_VAR or e[i:i + win].mean() < 0.01:  # not modulated enough / too quiet
            continue
        ac = np.correlate(seg, seg, "full")[win - 1:] / (var * win)
        per = float(ac[4:8].max())  # a burst every 0.2-0.35 s
        if per > LAUGH_PER:
            t = int(i * 0.05 + 0.75)
            if t < n:
                out[t] = max(out[t], min(2.0, (per - LAUGH_PER) * 5 + 0.5))
    return smooth(out, 3) * 3  # smoothed over a few seconds


LAUGH_VAR, LAUGH_PER = 0.15, 0.35


def z(a):
    a = np.asarray(a, dtype=float)
    if a.std() < 1e-9:
        return np.zeros_like(a)
    return (a - np.median(a)) / (a.std() + 1e-9)


def smooth(a, k):
    k = min(max(1, int(k)), max(1, len(a)))  # a very short source (a 20 s Twitch clip) is shorter than the window: shrink it, 'same' would return the longer array
    return np.convolve(a, np.ones(k) / k, mode="same")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src"); ap.add_argument("--top", type=int, default=8)
    ap.add_argument("--min", type=float, default=15); ap.add_argument("--max", type=float, default=40)
    ap.add_argument("--chat"); ap.add_argument("--max-minutes", type=int, default=90)
    ap.add_argument("--pre", type=float, default=PRE, help="seconds of lead-in before the event (live mode uses 5 so nothing before it is cut off)")
    ap.add_argument("--start", type=int, default=0, help="start N minutes into a long VOD (timestamps in candidates are then relative to the slice)")
    a = ap.parse_args()
    d = job_dir(a.src + (f"#t{a.start}" if a.start else ""))
    vid, info = fetch(a.src, d, a.max_minutes, a.start)
    dur = float((ffprobe(vid).get("format") or {}).get("duration") or 0)
    wav, db = audio_rms(vid, d)
    if dur <= 0:  # some live recordings carry no length in their header (a broken or ad-interrupted recording): measure the audio instead
        try:
            with wave.open(str(wav)) as wf:
                dur = wf.getnframes() / float(wf.getframerate())
        except Exception:
            dur = 0.0
        if dur <= 0:
            save_json(d / "candidates.json", {"source": str(vid), "info": load_json(d / "info.json"), "duration": 0, "candidates": [], "signals": {}})
            print("job: " + str(d) + chr(10) + "this recording has no readable length: skipping it" + chr(10) + "0 candidates (0 strong)")
            return
    n = len(db)
    words = transcribe(wav, d)
    lang = load_json(d / "lang.json") if (d / "lang.json").exists() else {"language": "en", "p": 1}
    try:
        from textutil import allowed_languages
        allowed = allowed_languages(json.loads((Path(__file__).resolve().parent.parent / "autopost_config.json").read_text(encoding="utf-8")))
    except Exception:
        allowed = ["en"]
    try:
        import langkit
        lang_reject = allowed is not None and not langkit.moment_allowed(lang, words, allowed)
    except Exception:
        lang_reject = allowed is not None and lang["language"] not in allowed and lang["p"] >= 0.5
    if lang_reject:  # config "languages": skip anything not picked, never cut it
        save_json(d / "candidates.json", {"source": str(vid), "info": load_json(d / "info.json"), "duration": dur, "candidates": [], "signals": {}})
        print(f"job: {d}\nLANGUAGE {lang['language']} ({lang['p']}) is not in your language list: skipping this source\n0 candidates (0 strong)")
        return
    # signals
    loud = smooth(PR.cap_z(z(db - smooth(db, 30))), 3)          # jumps above the local level
    hype = np.zeros(n)
    for w in words:
        if int(w["s"]) < n and (HYPE.search(w["w"]) or _xreact(w["w"], lang) == "hype"):
            hype[int(w["s"])] += 1
    hype = smooth(hype, 5)
    heat = np.zeros(n)
    off = (a.start or 0) * 60  # the heatmap is for the whole video; a sliced download starts later (the chat below is shifted the same way)
    for h in (info.get("heatmap") or []):
        i0, i1 = max(0, int(h["start_time"] - off)), min(n, int(math.ceil(h["end_time"] - off)))
        if i1 > i0:
            heat[i0:i1] = h["value"]
    chat, react = chat_series(d, n, a.chat, info, a.start * 60)
    rate = smooth(react, 10); share = rate / (smooth(chat, 10) + 3)  # reactions per second, and their share of all messages
    chatz = smooth(0.5 * PR.cap_z(z(rate), True) + 0.5 * PR.cap_z(z(share), True), 3)
    w_heat = 2.0 if heat.any() else 0
    w_chat = 3.5 if chat.any() else 0          # chat reaction is the primary signal; sound only backs it up
    w_loud = 0.8 if chat.any() else 1.2        # without chat (no replay/live chat) fall back to loudness
    motion = smooth(PR.cap_z(z(smooth(motion_series(vid, d, n), 5))), 3)
    # listening: how the voice sounds, laughter, swearing (= real excitement), something unusual being said, speech speeding up
    try:
        voice = voice_series(wav, n)
    except Exception as e:
        print(f"voice analysis unavailable ({str(e)[:60]})"); voice = np.zeros(n)
    laugh, swear, novel, wps = np.zeros(n), np.zeros(n), np.zeros(n), np.zeros(n)
    for w in words:
        t = int(w["s"])
        if t >= n:
            continue
        wps[t] += 1
        xr = _xreact(w["w"], lang)  # other languages' reaction words (only when that language was heard for sure)
        if LAUGH.match(w["w"].strip()) or xr == "laugh": laugh[t] += 1
        if SWEAR.search(w["w"]) or xr == "swear": swear[t] += 1
        if NOVEL.search(w["w"]): novel[t] += 1
    laugh, swear, novel = np.clip(smooth(laugh, 4) * 2, 0, 3), np.clip(smooth(swear, 4) * 3, 0, 2), np.clip(smooth(novel, 5) * 3, 0, 2)  # sparse events: raw strength, not z-scores
    try:  # laughing (and rapid excited bursts) heard in the sound, since the transcript rarely writes "haha": a light extra, never decisive on its own
        laugh = np.maximum(laugh, 0.35 * np.clip(laugh_audio(wav, n), 0, 2))
    except Exception as e:
        print(f"laughter detection unavailable ({str(e)[:60]})")
    rate_z = smooth(PR.cap_z(z(smooth(wps, 3))), 2)
    speaking = np.zeros(n)
    for w in words:
        speaking[max(0, int(w["s"])):min(n, int(w["e"]) + 1)] = 1
    speaking = smooth(speaking, 3) > 0  # someone is talking (short pauses between words and sentences do not count as silence)
    # weights: what the voice does and what happens on screen matter most; fast talking alone (mission chatter) barely counts
    score = (w_loud * loud + 0.4 * PR.cap_z(z(hype), True) + 0.8 * motion + w_heat * PR.cap_z(z(heat), True) + w_chat * chatz
             + 1.5 * voice + 0.8 * laugh + 0.8 * swear + 0.7 * novel + 0.15 * rate_z)
    # pick non-overlapping windows around peaks
    starts = [x["s"] for x in words]
    picks, used = [], np.zeros(n, bool)
    order = np.argsort(-score)
    for p in order:
        if len(picks) >= a.top or score[p] <= 0:
            break
        # energy gate: words and motion alone are not "interesting" (quiet streamer over a cutscene scored high before);
        # need a real audio spike, a chat spike or a most-replayed peak near this second
        near = slice(max(0, p - 4), p + 5)
        if not (chatz[near].max() > 1.0 or loud[near].max() > 1.0 or voice[near].max() > 1.0 or laugh[near].max() > 0.5
                or swear[near].max() > 0.5 or (w_heat and heat[near].max() > 0.5)):
            continue  # nothing exciting near this second (no chat spike, loud jump, excited voice, laughter, swearing or replay peak)
        # start right before the event instead of 15 s of build-up (the first seconds decide whether people stay): the event is the first loud
        # second in the 12 s before the peak (sound is instant, chat reacts 5-10 s later), the clip opens PRE seconds ahead of it
        onset = next((t for t in range(max(0, int(p) - 12), min(n, int(p) + 3)) if loud[t] > 1.0), None)
        ev = onset if onset is not None else max(0, int(p) - (8 if w_chat else 3))
        s0 = max(0, ev - a.pre); e0 = min(dur, s0 + a.max)
        # snap start to the first word after s0 and end to a word end, keep length in [min, max]
        ws = [x for x in words if x["s"] >= s0 and x["e"] <= e0]
        if ws:
            s0 = min(max(0, ws[0]["s"] - 0.3), max(0, ev - 1)); e0 = min(dur, ws[-1]["e"] + 0.6)  # never start after the event
        # let the moment play out, then stop: the clip keeps going while it is still "alive" (loud, an excited voice, laughter, swearing, action) and a
        # little longer while the talking about it continues; it ends 3 s after the last lively second when nobody speaks, or 8 s after it when only calm talk
        # is left. This is what makes a bit like "dislike... young studs only... gonna like it" run to its punchline instead of being cut mid-joke.
        pk = float(db[max(0, p - 3):p + 4].max())
        last_strong, end_t = int(p), None
        for t in range(int(p) + 1, min(n, int(s0 + a.max))):
            if loud[t] > 0.8 or voice[t] > 0.6 or laugh[t] > 0.3 or swear[t] > 0.3 or motion[t] > 3.0:  # (walking / driving always has some motion: only real action counts)
                last_strong = t
            if (t - last_strong >= 4 and not speaking[t]) or (t - last_strong >= 10):  # a moment that goes on is not cut: 4 s of nothing, or 10 s of only calm talk, ends it
                end_t = last_strong + (3 if speaking[t] else 1)
                break
        if end_t is not None:
            e0 = min(e0, end_t + 1.5)
            tail = [x for x in words if x["s"] < e0 < x["e"]]
            if tail:
                e0 = min(dur, tail[-1]["e"] + 0.4)  # finish the word that is being said
            # finish the thought: when the talking carries straight on (pauses under 1.2 s) let it run to the end of that sentence group, at most 10 s more
            # A GREAT moment (score 6+) that would end up short (under 26 s) gets more room: the next burst of speech is included even when it starts after a
            # pause of up to 9 s (the bit often carries on after a swipe, a cutscene beat or a breath), up to 12 s more.
            great = float(score[p]) >= 5.0 and (e0 - s0) < 40
            gap_ok, more = (9.0, 20) if great else (1.6, 14)
            ext, limit = e0, min(dur, s0 + a.max, e0 + more)
            for x in words:
                if x["s"] < e0 - 0.5:
                    continue
                if x["s"] > ext + gap_ok or x["e"] > limit:
                    break
                ext = max(ext, x["e"])
            e0 = min(limit, ext + 0.4)
        if e0 - s0 < a.min:  # too short now: take the missing seconds from the build-up before the peak, not from the aftermath
            s0 = max(0, s0 - (a.min - (e0 - s0)))
            mid = next((x for x in words if x["s"] < s0 < x["e"]), None)
            if mid:
                s0 = max(0, mid["s"] - 0.1)  # not in the middle of a word
            e0 = max(e0, min(dur, s0 + a.min))
        e0 = min(e0, s0 + a.max)
        try:
            import langkit
            if allowed is not None and not langkit.moment_allowed(lang, [x for x in words if s0 <= x["s"] < e0], allowed):
                continue
        except Exception:
            pass
        if used[int(s0):int(e0)].any():
            continue
        text = " ".join(x["w"] for x in words if s0 <= x["s"] < e0)
        talk_hits = len(TALK.findall(text))
        react = bool(voice[near].max() > 1.0 or laugh[near].max() > 0.5 or swear[near].max() > 0.5)  # a real reaction inside it
        if PR.talk_blocks(talk_hits, react):  # the streamer is talking to chat / about the stream, not reacting to something in the game
            continue
        if PROMO.search(text):  # giveaway / sponsor talk
            continue
        used[max(0, int(s0) - 12):int(e0) + 12] = True  # picks stay at least 12 s apart: no two clips of the same moment (set only once the moment is accepted: a rejected one must not block its neighbours)
        ctx = " ".join(x["w"] for x in words if s0 - 45 <= x["s"] < e0 + 45)  # speech around the moment
        hits = len(GAME.findall(ctx))
        bonus = (0.2 * min(hits, 3) - (0.8 if hits == 0 and len(ctx.split()) >= 25 else 0.0)) if GTA_TOPIC else 0.0  # the word bonus is written for GTA; other topics get none  # in the game = a little more, long non-game talk = a little less
        agree = sum(1 for arr, th in ((chatz, 1.0), (heat, 0.5), (motion, 3.0)) if arr[near].max() > th)  # independent signals that fire together
        agree += 1 if (loud[near].max() > 1.0 or voice[near].max() > 1.0) else 0  # loudness and an excited voice are the same sound: one signal
        agree += 1 if laugh[near].max() > 0.5 else 0
        agree += 0 if talk_hits < 2 else -1  # a reaction that is mostly talking to chat counts for less
        final = float(score[p]) + bonus + min(1.05, 0.35 * max(0, agree - 1))  # one signal alone is easy to fool (a raid, a loud game sound): agreement is worth more
        why = []
        if agree >= 3: why.append(f"{agree} signals agree")
        if loud[p] > 1: why.append("loudness spike")
        if hype[p] > 0: why.append("hype words")
        if motion[p] > 1: why.append("action on screen")
        if heat[p] > 0.5: why.append(f"most replayed {heat[p]:.2f}")
        if w_chat and chatz[p] > 1: why.append("chat spike")
        if voice[near].max() > 1: why.append("excited voice")
        if laugh[near].max() > 0.5: why.append("laughter")
        if swear[near].max() > 0.5: why.append("swearing")
        if novel[near].max() > 0.5: why.append("something unusual")
        if hits: why.append("in the game")
        picks.append({"n": len(picks) + 1, "start": round(s0, 2), "end": round(e0, 2), "peak": int(p),
                      "score": round(final, 2), "strong": bool(final >= STRONG), "why": why or ["overall energy"], "text": text[:400],
                      "sig": {k: round(float(v[near].max()), 2) for k, v in (("loud", loud), ("hype", hype), ("motion", motion), ("voice", voice), ("laugh", laugh), ("swear", swear), ("novel", novel), ("rate", rate_z), ("chat", chatz))} | {"game": hits, "bonus": round(bonus, 2)}})
    picks.sort(key=lambda x: x["start"])
    for i, x in enumerate(picks, 1):
        x["n"] = i
    meta = load_json(d / "info.json")
    save_json(d / "candidates.json", {"source": str(vid), "info": meta, "duration": dur, "candidates": picks,
                                      "signals": {"heatmap": bool(w_heat), "chat": bool(w_chat), "words": len(words)}})
    fmt = lambda t: f"{int(t // 60)}:{int(t % 60):02d}"
    md = [f"# Candidates: {meta.get('title', '')}", f"source: {meta.get('webpage_url', '')}",
          f"signals: loudness, transcript ({len(words)} words)" + (", most replayed" if w_heat else "") + (", chat" if w_chat else ""), ""]
    for x in picks:
        md += [f"## {x['n']}. {fmt(x['start'])}-{fmt(x['end'])} ({x['end'] - x['start']:.0f}s) score {x['score']}" + ("" if x["strong"] else "  [WEAK: below the post-worthy bar]"),
               f"why: {', '.join(x['why'])}", f"> {x['text']}", ""]
    (d / "candidates.md").write_text("\n".join(md), encoding="utf-8")
    good = sum(1 for x in picks if x["strong"])
    print(f"job: {d}\n{len(picks)} candidates ({good} strong, score >= {STRONG}) -> {d / 'candidates.md'}")
    if not good:
        print("NO STRONG MOMENTS in this source: do not post from it, try another video or another slice (--start)")


if __name__ == "__main__":
    main()
