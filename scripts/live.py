"""Record a YouTube or Twitch (twitch.tv/<login>, no key) live stream and auto-cut clips when live chat spikes.
Twitch chat is read via anonymous IRC (twitch.py) instead of yt-dlp live_chat.

Records the stream in 60 s segments into jobs/<id>/live/, reads live chat as it arrives, and on a chat
spike cuts [spike-before, spike+after] into live/spike_<n>.mp4 and appends a row to live/spikes.md.
Run moments.py / cut.py on the spike clips afterwards.

usage: python live.py <youtube-live-url> --minutes 60 [--before 15 --after 20 --window 10 --baseline 300
                      --ratio 3 --min-msgs 8 --lag 8 --cooldown 45]
Stop early with Ctrl+C. Pending spikes are still cut on shutdown.
"""
import argparse, json, re, subprocess, sys, time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from common import job_dir, run, save_json, tool, ytdlp_base
from textutil import chat_weight

HYPE = re.compile(r"(?i:LMAO|KEKW|LOL|CLIP|HOLY|NO WAY)|\bW+\b|[💀😂🔥]")
NOISE = re.compile(r"(?i)^!|giveaway|\braid(ing|ed)?\b|\bbrb\b|be right back|ad break|sponsor|subscribe|discord\.gg|https?://|promo code|follow me")
STOP = set("the a an and or to of is it in i you that this on for with was are be so but not my me we he she they what just like at as if do get".split())
WORD = re.compile(r"[\w']{2,}|[💀😂🔥]")


def now():
    return time.time()


def extract_text(renderer):
    out = []
    for r in (renderer.get("message") or {}).get("runs", []):
        if "text" in r:
            out.append(r["text"])
        elif "emoji" in r:
            e = r["emoji"]
            out.append(e.get("emojiId") if len(e.get("emojiId", "")) <= 2 else (e.get("shortcuts") or [""])[0])
    return "".join(out).strip()


class ChatTail:
    """Tails yt-dlp's live_chat output (.part, .part-FragN or final file) and collects per-second counts."""

    def __init__(self, d, start_wall):
        self.d, self.start = d, start_wall
        self.pos, self.buf, self.seen = {}, {}, set()
        self.raw, self.wgt, self.words = Counter(), Counter(), {}  # keyed by int unix second

    def poll(self):
        for f in sorted(self.d.glob("chat.live_chat.json*")):
            try:
                with open(f, "rb") as fh:
                    fh.seek(self.pos.get(f.name, 0))
                    data = fh.read()
            except OSError:
                continue
            if not data:
                continue
            self.pos[f.name] = self.pos.get(f.name, 0) + len(data)
            buf = self.buf.get(f.name, b"") + data
            *lines, rest = buf.split(b"\n")
            self.buf[f.name] = rest
            for ln in lines:
                self.handle(ln)

    def handle(self, ln):
        try:
            acts = json.loads(ln)["replayChatItemAction"]["actions"]
        except Exception:
            return
        for a in acts:
            r = (((a.get("addChatItemAction") or {}).get("item")) or {}).get("liveChatTextMessageRenderer")
            if not r or r.get("id") in self.seen:
                continue
            self.seen.add(r.get("id"))
            try:
                ts = int(r["timestampUsec"]) / 1e6
            except Exception:
                ts = now()
            txt = extract_text(r)
            if ts < self.start - 5 or not txt or NOISE.search(txt):  # history backlog / ads / raids
                continue
            s = int(ts)
            self.raw[s] += 1
            self.wgt[s] += {3: 1.0, 2: 0.5}.get(chat_weight(txt), 0.0)  # only real reactions count, plain chatter does not
            self.words.setdefault(s, []).append(txt)

    def total(self, c, a, b):
        return sum(c.get(s, 0) for s in range(a, b + 1))

    def top_words(self, a, b, n=3):
        cnt = Counter()
        for s in range(a, b + 1):
            for t in self.words.get(s, []):
                cnt.update(w.lower() for w in WORD.findall(t) if w.lower() not in STOP)
        return [w for w, _ in cnt.most_common(n)]


class TwitchTail(ChatTail):
    """Same counters as ChatTail, fed from the JSON lines twitch.irc_chat appends to chat.twitch.jsonl."""

    def poll(self):
        f = self.d / "chat.twitch.jsonl"
        try:
            with open(f, "rb") as fh:
                fh.seek(self.pos.get(f.name, 0))
                data = fh.read()
        except OSError:
            return
        if not data:
            return
        self.pos[f.name] = self.pos.get(f.name, 0) + len(data)
        buf = self.buf.get(f.name, b"") + data
        *lines, rest = buf.split(b"\n")
        self.buf[f.name] = rest
        for ln in lines:
            try:
                m = json.loads(ln)
                ts, txt = float(m["t"]), (m["text"] or "").strip()
            except Exception:
                continue
            if ts < self.start - 5 or not txt or NOISE.search(txt):
                continue
            s = int(ts)
            self.raw[s] += 1
            self.wgt[s] += {3: 1.0, 2: 0.5}.get(chat_weight(txt), 0.0)  # only real reactions count, plain chatter does not
            self.words.setdefault(s, []).append(txt)


def twitch_login(url):
    m = re.match(r"https?://(?:www\.|m\.)?twitch\.tv/([A-Za-z0-9_]+)/?(?:[?#].*)?$", url)
    return m.group(1).lower() if m and m.group(1).lower() not in ("videos", "directory") else None


class Recorder:
    def __init__(self, ld, url, minutes):
        self.ld, self.csv = ld, ld / "segments.csv"
        r = run(ytdlp_base(url) + ["-g", "-f", "b[height<=720]/bv*[height<=720]+ba/b", url], capture=True)
        urls = [u for u in r.stdout.split() if u.startswith("http")]
        if not urls:
            sys.exit("could not get a stream URL (is the video live?)")
        cmd = [tool("ffmpeg"), "-y", "-loglevel", "warning"]
        for u in urls:
            cmd += ["-fflags", "+genpts+discardcorrupt", "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5", "-i", u]
        for i in range(len(urls)):
            cmd += ["-map", str(i)]
        cmd += ["-c", "copy", "-max_interleave_delta", "0", "-t", str(int(minutes * 60)), "-f", "segment", "-segment_time", "60",
                "-segment_format", "mpegts", "-reset_timestamps", "1", "-segment_list", str(self.csv),
                "-segment_list_type", "csv", str(ld / "seg_%05d.ts")]
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        self.t0 = None

    def alive(self):
        return self.proc.poll() is None

    def stop(self):
        if self.alive():
            try:
                self.proc.stdin.write(b"q\n"); self.proc.stdin.flush()
            except OSError:
                pass
            try:
                self.proc.wait(20)
            except subprocess.TimeoutExpired:
                self.proc.terminate()
                try: self.proc.wait(5)
                except subprocess.TimeoutExpired: self.proc.kill()

    def segments(self):
        """[(path, start, end, mtime, complete)] with cumulative timeline starts (seconds since recording began)."""
        done, t = [], 0.0
        if self.csv.exists():
            for ln in self.csv.read_text(errors="ignore").splitlines():
                p = ln.split(",")
                if len(p) >= 3 and (self.ld / p[0]).exists():
                    dur = float(p[2]) - float(p[1])
                    f = self.ld / p[0]
                    done.append((f, t, t + dur, f.stat().st_mtime, True)); t += dur
        names = {s[0].name for s in done}
        for f in sorted(self.ld.glob("seg_*.ts")):
            if f.name not in names:  # in-progress segment: end unknown, treat as open-ended
                done.append((f, t, float("inf"), f.stat().st_mtime, False))
        return done


def cut(rec, wall, before, after, lag, out):
    """Cut around wall-clock time `wall`. Returns (offset_seconds, ok)."""
    segs = rec.segments()
    comp = [s for s in segs if s[4]]
    if not comp:
        return None, False
    f, st, en, mt, _ = comp[-1]
    center = en - (mt - wall) + lag  # stream position of the event: reception time of last full segment anchors the clock
    t0, t1 = max(0.0, center - before), center + after
    pick = [s for s in segs if s[2] > t0 and s[1] < t1]
    if not pick:
        return center, False
    lst = out.with_suffix(".txt")
    lst.write_text("".join(f"file '{s[0].resolve().as_posix()}'\n" for s in pick), encoding="utf-8")
    r = run([tool("ffmpeg"), "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
             "-ss", f"{max(0.0, t0 - pick[0][1]):.2f}", "-t", f"{t1 - t0:.2f}", "-c", "copy",
             "-avoid_negative_ts", "make_zero", "-movflags", "+faststart", str(out)], check=False, capture=True)
    lst.unlink(missing_ok=True)
    return center, r.returncode == 0 and out.exists() and out.stat().st_size > 0


# giveaway / promo chatter: people typing a keyword to take part, not reacting to the game
PARTICIPATE = re.compile(r"(?i)^(enter|join|me+|pick me|raffle|claim|code|\+1|1)$|\b(enter(ed)?|raffle|claim|promo|giveaways?)\b")


def window_sum(c, end, window):
    return sum(c.get(s, 0) for s in range(end - window + 1, end + 1))


def judge_spike(tail, e, window, viewers, elapsed):
    """Is the last `window` seconds of chat a real reaction? Adapts to the stream: the bar is built from THIS chat's own last 10 minutes and
    from the viewer count, so 100 messages is huge for a 3k stream and nothing for an 80k one.
    returns ("none"|"ignored"|"spike", info). Reaction points = chat_weight shock 3 / laugh 2 (see textutil), plain messages 0."""
    from collections import Counter
    from textutil import chat_weight
    w = window_sum(tail.wgt, e, window); raw = window_sum(tail.raw, e, window)
    # small floor only: chat VOLUME does not follow viewer count (measured: 172k viewers on YouTube = 4.6 msgs/s, 57k on Twitch = 20+ msgs/s), so the
    # real bar is statistical (below): 4 standard deviations above THIS chat's own normal and 3x its average
    min_react = max(6.0, viewers / 20000.0)
    if w < min_react:
        return "none", {}
    hist = min(600, elapsed - window)
    if hist < 120:                                   # need a couple of minutes of this chat to know what normal looks like
        return "none", {}
    pts = [(window_sum(tail.wgt, e - window - k, window), window_sum(tail.raw, e - window - k, window)) for k in range(0, hist - window, 5)]
    ws = [p[0] for p in pts]
    mean = sum(ws) / len(ws); std = (sum((x - mean) ** 2 for x in ws) / len(ws)) ** 0.5
    thr = max(mean + 4 * max(std, 0.3 * mean + 1), 3 * mean)     # 4 standard deviations above this chat's normal AND at least 3x its average
    if w < thr:
        return "none", {}
    share, base_share = w / max(1, raw), sum(ws) / max(1, sum(p[1] for p in pts))
    msgs = [m for s2 in range(e - window + 1, e + 1) for m in tail.words.get(s2, [])]
    info = {"points": round(w, 1), "normal": round(mean, 1), "needed": round(thr, 1), "messages": raw, "viewers": int(viewers), "reaction_share": round(share, 2)}
    if not msgs:
        return "ignored", {**info, "reason": "no message text"}
    if share < max(0.15, 2 * base_share):
        return "ignored", {**info, "reason": f"only {share:.0%} of messages are reactions (normal here {base_share:.0%})"}
    cnt = Counter(m.lower().strip() for m in msgs); dom, domn = cnt.most_common(1)[0]
    if domn / len(msgs) > 0.5 and chat_weight(dom) == 1:  # one repeated word is fine when it IS the reaction (WTF, OMG, NOOO...), spam when it is anything else
        return "ignored", {**info, "reason": f"{domn} of {len(msgs)} messages are the same non-reaction text ({dom[:20]!r})"}
    part = sum(1 for m in msgs if PARTICIPATE.search(m.strip())) / len(msgs)
    if part > 0.25:
        return "ignored", {**info, "reason": f"{part:.0%} look like giveaway/promo entries"}
    longm = [re.sub(r"\W+", "", m.lower()) for m in msgs if len(m.split()) >= 4]  # long identical messages = copy-paste / bots
    spam = (len(longm) - len(set(longm))) / len(msgs)
    if spam > 0.3:
        return "ignored", {**info, "reason": f"{spam:.0%} copy-paste messages"}
    react = [m for m in msgs if chat_weight(m) >= 2]
    if len(react) < 4:
        return "ignored", {**info, "reason": f"only {len(react)} reaction messages"}
    return "spike", {**info, "spam": round(spam, 2), "reactions": len(react), "sample": [re.sub(r"https?://\S+", "", m)[:40].strip() for m in react[:14]]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url"); ap.add_argument("--minutes", type=float, default=60)
    ap.add_argument("--before", type=float, default=15); ap.add_argument("--after", type=float, default=20)
    ap.add_argument("--window", type=int, default=10); ap.add_argument("--baseline", type=int, default=300)
    ap.add_argument("--ratio", type=float, default=3); ap.add_argument("--min-msgs", type=int, default=8)
    ap.add_argument("--cooldown", type=float, default=45)
    ap.add_argument("--viewers", type=int, default=0, help="viewer count: scales the minimum reaction size")
    ap.add_argument("--lag", type=float, default=8, help="seconds to shift the clip later: stream delay + chat reaction time")
    a = ap.parse_args()

    d = job_dir(a.url)
    ld = d / "live"
    ld.mkdir(exist_ok=True)
    for f in list(ld.glob("seg_*.ts")) + list(ld.glob("chat.live_chat.json*")) + [ld / "segments.csv", ld / "chat.twitch.jsonl"]:
        f.unlink(missing_ok=True)
    spikes_md = ld / "spikes.md"
    if not spikes_md.exists():
        spikes_md.write_text("| UTC time | offset | msgs/%ds | top words | file |\n|---|---|---|---|---|\n" % a.window, encoding="utf-8")
    n = len(list(ld.glob("spike_*.mp4")))
    start = now()
    end_at = start + a.minutes * 60 + 30

    rec = Recorder(ld, a.url, a.minutes)
    login = twitch_login(a.url)
    if login:
        import threading, twitch
        stop_chat = threading.Event()
        chat_thread = threading.Thread(target=twitch.irc_chat, args=(login, ld / "chat.twitch.jsonl", stop_chat), daemon=True)
        chat_thread.start()
        chat = None
        tail = TwitchTail(ld, start)
    else:
        chat = subprocess.Popen(ytdlp_base() + ["--skip-download", "--write-subs", "--sub-langs", "live_chat",
                                                "-o", str(ld / "chat"), a.url],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        tail = ChatTail(ld, start)
    pending, last_spike, spikes, chat_info = [], 0.0, 0, {}
    print(f"recording {a.minutes:g} min -> {ld}  (Ctrl+C to stop)", flush=True)

    def finish_one(item, final=False):
        nonlocal spikes
        num, wall, rate, words = item
        out = ld / f"spike_{num}.mp4"
        center, ok = cut(rec, wall, a.before, a.after, a.lag, out)
        out.with_suffix(".json").write_text(json.dumps(chat_info.get(num, {}), ensure_ascii=False), encoding="utf-8")  # what chat said at this spike (used by the AI check and titles)
        iso = datetime.fromtimestamp(wall, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        off = "-" if center is None else f"{int(center // 60)}:{int(center % 60):02d}"
        row = f"| {iso} | {off} | {rate} | {', '.join(words)} | {out.name if ok else 'CUT FAILED'} |\n"
        with open(spikes_md, "a", encoding="utf-8") as fh:
            fh.write(row)
        print(("clip " + out.name if ok else "cut failed") + f" (spike at {off}, {rate} msgs/{a.window}s, {words})", flush=True)

    try:
        while now() < end_at and rec.alive():
            time.sleep(1)
            tail.poll()
            t = now()
            if rec.t0 is None and any(ld.glob("seg_*.ts")):
                rec.t0 = t
            e = int(t) - 3  # let chat batches land before judging a second
            elapsed_chat = e - int(start)
            if elapsed_chat >= a.window + 30 and t - last_spike >= a.cooldown and rec.t0:
                verdict, info = judge_spike(tail, e, a.window, a.viewers, elapsed_chat)
                if verdict == "ignored":
                    print(f"spike ignored: {info['reason']} ({info['points']} reaction points vs normal {info['normal']}, {info['messages']} messages)", flush=True); last_spike = t
                elif verdict == "spike":
                    n += 1; spikes += 1; last_spike = t
                    raw = info["messages"]
                    chat_info[n] = info
                    words = tail.top_words(e - a.window + 1, e)
                    print(f"SPIKE #{n}: {info['points']} reaction points vs normal {info['normal']} (needed {info['needed']}), {raw} messages, {info['viewers']} viewers, {info['reactions']} reactions", flush=True)
                    pending.append((n, float(e), raw, words, t + a.after + max(a.lag, 0)))
            for p in [p for p in pending if p[4] <= t]:
                pending.remove(p); finish_one(p[:4])
    except KeyboardInterrupt:
        print("stopping...", flush=True)
    finally:
        rec.stop()
        if login:
            stop_chat.set()
            chat_thread.join(5)
        elif chat.poll() is None:
            chat.terminate()
            try: chat.wait(10)
            except subprocess.TimeoutExpired: chat.kill()
        for p in pending:
            finish_one(p[:4], final=True)
        save_json(ld / "live.json", {"url": a.url, "spikes": spikes, "minutes": a.minutes})
    print(f"done: {spikes} spike(s). See {spikes_md}")


if __name__ == "__main__":
    main()
