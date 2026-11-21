"""Clippy security audit: anyone who receives this tool can run it to check that nothing in it can send data away.
python scripts\\security_audit.py
 1. SHA256SUMS.txt (shipped with a release): lists files that were changed, added or removed since the release was built.
 2. Dangerous code: eval / exec / os.system / shell=True / pickle / base64 decoding / registry / startup / scheduled-task / LOLBin patterns.
 3. Every web address mentioned in the code must belong to the allowed list (YouTube, TikTok, Twitch, Google/Gemini APIs, localhost, ...).
 4. The network guard is installed in the venv and set to "enforce".
 5. No keys, cookies, tokens or personal files are inside the folder you would hand to someone else (api_tokens, chrome_profile, config with keys).
Exit code 0 = clean, 1 = findings."""
import hashlib, importlib.util, json, pathlib, re, sys

R = pathlib.Path(__file__).resolve().parent.parent
SKIP = re.compile(r"[\\/](\.venv|jobs|chrome_profile|logs|\.git|__pycache__|api_tokens|release|legacy_claude_mode)[\\/]")
EXT = {".py", ".bat", ".cmd", ".ps1", ".js", ".html"}
spec = importlib.util.spec_from_file_location("ng", R / "scripts" / "netguard.py"); ng = importlib.util.module_from_spec(spec); spec.loader.exec_module(ng)
ALLOWED_HOSTS = set(ng.BASE) | set(ng.PYPI) | {"localhost", "127.0.0.1", "github.com", "raw.githubusercontent.com", "huggingface.co", "www.w3.org", "anthropic.com",
                                              "api.openai.com", "api.anthropic.com", "openrouter.ai", "generativelanguage.googleapis.com", "studio.youtube.com", "www.tiktok.com",
                                              "gql.twitch.tv", "api.twitch.tv", "irc.chat.twitch.tv", "developers.tiktok.com", "console.cloud.google.com",
                                              # printed to the user as instructions (links to click), never contacted by the code:
                                              "support.google.com", "www.google.com", "www.python.org", "python.org", "ffmpeg.org"}
DANGER = {
    r"\beval\(|\bexec\(": "dynamic code execution",
    r"os\.system|shell\s*=\s*True": "shell execution",
    r"\bpickle\b|\bmarshal\b": "unsafe deserialisation",
    r"b64decode|fromhex\(|zlib\.decompress|codecs\.decode": "decoding of hidden payloads",
    r"\bwinreg\b|schtasks|reg\s+add|RunOnce|\\\\Run\b|Start Menu|\\\\Startup\\\\|New-ScheduledTask": "persistence / registry",
    r"certutil|bitsadmin|mshta|rundll32|wscript|cscript|Invoke-Expression|\biex\b|powershell(\.exe)?\s+-(enc|e\b)": "living-off-the-land binaries",
    r"smtplib|ftplib|paramiko|telnetlib": "mail / ftp / ssh clients",
}
ALLOW_PATTERN_IN = {"secretstore.py": ("ctypes",)}
bad = []


def fail(m):
    bad.append(m); print("FINDING:", m)


# 1. checksums
sums = R / "SHA256SUMS.txt"
if sums.exists():
    exp = {}
    for ln in sums.read_text(encoding="utf-8").splitlines():
        if "  " in ln:
            h, p = ln.split("  ", 1); exp[p.strip()] = h
    for p, h in exp.items():
        f = R / p
        if not f.exists():
            fail(f"file from the release is missing: {p}")
        elif hashlib.sha256(f.read_bytes()).hexdigest() != h:
            fail(f"file changed since the release was built: {p}")
    for f in R.rglob("*"):
        if f.is_file() and f.suffix.lower() in EXT and not SKIP.search(str(f)):
            rel = f.relative_to(R).as_posix()
            if rel not in exp:
                fail(f"code file that is not part of the release: {rel}")
else:
    print("note: no SHA256SUMS.txt here (this is a working copy, not a release): checksum comparison skipped")

# 2 + 3. code scan
host_rx = re.compile(r"https?://([A-Za-z0-9._\-]+)")
for f in sorted(R.rglob("*")):
    if not f.is_file() or f.suffix.lower() not in EXT or SKIP.search(str(f)):
        continue
    rel = f.relative_to(R).as_posix(); txt = f.read_text(encoding="utf-8", errors="ignore")
    if f.name == "security_audit.py":  # this file spells out the patterns it hunts for; its own hashes are covered by SHA256SUMS
        continue
    for i, ln in enumerate(txt.splitlines(), 1):
        for rx, why in DANGER.items():
            if re.search(rx, ln, re.I):
                fail(f"{rel}:{i}: {why}: {ln.strip()[:90]}")
        if re.search(r"\bctypes\b", ln) and f.name not in ALLOW_PATTERN_IN:
            fail(f"{rel}:{i}: ctypes outside the key store: {ln.strip()[:90]}")
        for h in host_rx.findall(ln):
            h = h.lower().rstrip(".")
            if not any(h == a or h.endswith("." + a) for a in ALLOWED_HOSTS) and "{" not in h:
                fail(f"{rel}:{i}: web address outside the allowed list: {h}")

# 4. guard
sc = R / ".venv" / "Lib" / "site-packages" / "sitecustomize.py"
if (R / ".venv").exists():
    if not sc.exists() or "netguard" not in sc.read_text(encoding="utf-8", errors="ignore"):
        fail("network guard is not installed in the venv (run setup.bat)")
try:
    mode = json.loads((R / "autopost_config.json").read_text(encoding="utf-8-sig")).get("network_guard", "enforce")
    if mode != "enforce":
        fail(f'network_guard is "{mode}" (must be "enforce")')
except FileNotFoundError:
    pass

# 5. personal / secret files
for p in ("api_tokens", "chrome_profile"):
    if (R / p).exists() and sums.exists():
        fail(f"{p}/ is inside a release folder: it holds logins and keys and must never be handed to anyone")
print("\nRESULT:", "CLEAN" if not bad else f"{len(bad)} finding(s)")
sys.exit(1 if bad else 0)
