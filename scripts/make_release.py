"""Build a clean, shareable Clippy release: release\\Clippy-<date>.zip (+ the same folder unpacked in release\\Clippy).
python scripts\\make_release.py
Only an explicit whitelist of files goes in. NEVER included: your config, state, logs, API keys, cookies / the signed-in Chrome profile, downloaded videos, clips.
The build stops if any file mentions your Windows user name, a channel name or an absolute path. A SHA256SUMS.txt lists every file, and the receiver can run
scripts\\security_audit.py to verify that nothing was changed or added and that no code can send data anywhere but the allowed platforms."""
import datetime as dt, hashlib, pathlib, re, shutil, subprocess, sys, zipfile

R = pathlib.Path(__file__).resolve().parent.parent
OUT = R / "release"
STAGE = OUT / "Clippy"
FILES = ["dashboard.py", "dashboard.html", "dashboard.bat", "setup.bat", "tick.py", "requirements.txt", "requirements-extra.txt", "requirements.lock.txt",
         "LICENSE", "README_CLIPPY.md", ".gitignore",
         "scripts/ai_check.py", "scripts/caption_check.py", "scripts/ai_title.py", "scripts/api_upload.py", "scripts/autorun.py", "scripts/check.py", "scripts/cleanup.py", "scripts/common.py",
         "scripts/cut.py", "scripts/facecam.py", "scripts/friendly.py", "scripts/init_config.py", "scripts/live.py", "scripts/live_watch.py", "scripts/make_meta.py", "scripts/make_release.py",
         "scripts/moments.py", "scripts/netguard.py", "scripts/post_clip.py", "scripts/progress.py", "scripts/secretstore.py", "scripts/security_audit.py",
         "scripts/sitecustomize_clippy.py", "scripts/stats.py", "scripts/streams.py", "scripts/textutil.py", "scripts/topic.py", "scripts/twitch.py", "scripts/learn.py", "scripts/views.py", "scripts/health.py", "scripts/ytlimit.py", "scripts/dupes.py", "scripts/langkit.py", "scripts/stream_lang.py", "scripts/selftest.py", "scripts/update_clippy.py", "scripts/update_ytdlp.py",
         "scripts/upload.py", "scripts/visual_check.py"]
FACE = pathlib.Path.home() / "AppData" / "Local" / "gtavi-claudecode" / "models" / "face_detection_yunet_2023mar.onnx"
# words that must never reach a release (your user name, channel name...): read from .release_scrub.txt in the project, a local file that is never packaged;
# the user name of this Windows account is always included, and Windows user-folder paths are always scrubbed.
_scrub = R / ".release_scrub.txt"
_terms = [t.strip() for t in (_scrub.read_text(encoding="utf-8").splitlines() if _scrub.exists() else []) if t.strip()]
_terms += [__import__("os").environ.get("USERNAME", "")]
PERSONAL = re.compile("|".join([re.escape(t) for t in _terms if t] + [r"[A-Za-z]:\\\\Users\\\\", r"[A-Za-z]:/Users/"]), re.I)

if STAGE.exists():
    shutil.rmtree(STAGE)
STAGE.mkdir(parents=True)
problems = []
for rel in FILES:
    src = R / rel
    if not src.exists():
        problems.append(f"missing: {rel}"); continue
    dst = STAGE / rel; dst.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(src, dst)
    if src.suffix.lower() in {".py", ".bat", ".html", ".md", ".txt"}:
        for i, ln in enumerate(src.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if PERSONAL.search(ln.replace("FrostyDog222/Clippy", "")) and rel != "scripts/make_release.py":
                problems.append(f"personal data in {rel}:{i}: {ln.strip()[:80]}")
if FACE.exists():  # the face model travels with the release (tiny, checked against a pinned hash): no download needed
    (STAGE / "models").mkdir(exist_ok=True); shutil.copyfile(FACE, STAGE / "models" / FACE.name)
if problems:
    print("RELEASE STOPPED:"); [print(" -", p) for p in problems]; sys.exit(1)

sums = []
for f in sorted(p for p in STAGE.rglob("*") if p.is_file()):
    if f.name == "SHA256SUMS.txt":
        continue
    sums.append(f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.relative_to(STAGE).as_posix()}")
(STAGE / "SHA256SUMS.txt").write_text("\n".join(sums) + "\n", encoding="utf-8")
zp = OUT / f"Clippy-{dt.date.today().isoformat()}.zip"
with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
    for f in sorted(p for p in STAGE.rglob("*") if p.is_file()):
        z.write(f, f"Clippy/{f.relative_to(STAGE).as_posix()}")
print(f"release built: {zp} ({zp.stat().st_size // 1024} KB, {len(sums)} files)")
r = subprocess.run([sys.executable, str(STAGE / "scripts" / "security_audit.py")], capture_output=True, text=True, cwd=STAGE)
print(r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-200:])
sys.exit(r.returncode)
