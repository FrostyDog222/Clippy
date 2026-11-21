"""Which language is spoken on a live stream? Records about 20 seconds of its audio and lets the speech model (the same one the clips use) decide.
python scripts/stream_lang.py <stream url>   -> prints one JSON line {"language": "en", "p": 0.97}, or {} when it could not be told. Nothing is stored."""
import json, subprocess, sys, tempfile, wave
from pathlib import Path

import numpy as np

SECONDS = 20


def main():
    url = sys.argv[1]
    from yt_dlp import YoutubeDL
    with YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True, "format": "bestaudio/best", "socket_timeout": 20}) as y:
        info = y.extract_info(url, download=False) or {}
    if info.get("language"):  # the uploader set it: no need to listen
        print(json.dumps({"language": str(info["language"]).split("-")[0].lower(), "p": 0.9})); return
    media = info.get("url") or next((f.get("url") for f in reversed(info.get("formats") or []) if f.get("url")), None)
    if not media:
        print("{}"); return
    with tempfile.TemporaryDirectory() as d:
        wav = Path(d) / "a.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-t", str(SECONDS), "-i", media, "-vn", "-ac", "1", "-ar", "16000", str(wav)], capture_output=True, timeout=90)
        if not wav.exists():
            print("{}"); return
        with wave.open(str(wav)) as w:
            pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    if len(pcm) < 16000 * 5:
        print("{}"); return
    from faster_whisper import WhisperModel
    try:
        import ctranslate2
        cuda = ctranslate2.get_cuda_device_count() > 0
    except Exception:
        cuda = False
    model = WhisperModel("small", device="cuda" if cuda else "cpu", compute_type="float16" if cuda else "int8")
    _, tinfo = model.transcribe(pcm, beam_size=1, vad_filter=True, without_timestamps=True)
    print(json.dumps({"language": tinfo.language, "p": round(float(tinfo.language_probability), 2)}))


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        print("{}")
