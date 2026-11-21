"""Keeps a short raw piece of a video clip (no captions) next to the finished clip so Crop / Rebuild without captions work without downloading the video again.
make_kit() runs when a clip goes to Ready to post; restore_job() rebuilds a small job folder from it when the clip is cut again. Both are deleted with the clip.
Times inside the piece are shifted: piece time = video time - off."""
import json, shutil, subprocess
from pathlib import Path

PAD = 6.0  # seconds kept before and after the clip


def _tool(name):
    from common import tool
    return tool(name)


def make_kit(job, d, pick, raw_out, kit_out):
    """job: the job folder of the clip, d: the finished clip's json, pick: candidate number. True when the piece + kit were written."""
    job = Path(job)
    c = json.loads((job / "candidates.json").read_text(encoding="utf-8"))
    src = Path(c["source"])
    if not src.exists():
        return False
    off = max(0.0, float(d["start"]) - PAD); end = float(d["end"]) + PAD
    r = subprocess.run([_tool("ffmpeg"), "-y", "-v", "error", "-ss", f"{off:.2f}", "-to", f"{end:.2f}", "-i", str(src), "-c:v", "libx264", "-preset", "veryfast", "-crf", "12",
                        "-c:a", "copy", "-f", "mp4", str(raw_out)], capture_output=True, text=True, timeout=600, creationflags=0x08000000)
    if r.returncode != 0 or not Path(raw_out).exists():
        Path(raw_out).unlink(missing_ok=True)
        return False
    cand = dict(next(x for x in c["candidates"] if x["n"] == pick))
    cand["start"] = round(cand["start"] - off, 2); cand["end"] = round(cand["end"] - off, 2)
    words = [{**w, "s": round(w["s"] - off, 2), "e": round(w["e"] - off, 2)} for w in json.loads((job / "transcript.json").read_text(encoding="utf-8")) if off - 1 <= w["s"] <= end + 1]
    lang = json.loads((job / "lang.json").read_text(encoding="utf-8")) if (job / "lang.json").exists() else None
    Path(kit_out).write_text(json.dumps({"off": off, "pick": pick, "cand": cand, "info": c.get("info", {}), "words": words, "lang": lang}, ensure_ascii=False), encoding="utf-8")
    return True


def restore_job(kit_file, raw_file, workdir):
    """-> (job folder, start, end) in piece time, ready for cut.py --pick <pick> --start --end."""
    k = json.loads(Path(kit_file).read_text(encoding="utf-8")); workdir = Path(workdir); workdir.mkdir(parents=True, exist_ok=True)
    src = workdir / "source.mp4"
    shutil.copy2(raw_file, src)
    (workdir / "candidates.json").write_text(json.dumps({"source": str(src), "info": k["info"], "candidates": [k["cand"]]}, ensure_ascii=False), encoding="utf-8")
    (workdir / "transcript.json").write_text(json.dumps(k["words"], ensure_ascii=False), encoding="utf-8")
    if k.get("lang"):
        (workdir / "lang.json").write_text(json.dumps(k["lang"]), encoding="utf-8")
    return workdir, k["cand"]["start"], k["cand"]["end"], k["off"], k["pick"]
