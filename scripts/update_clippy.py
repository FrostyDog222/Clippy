"""Check for / install Clippy updates from the public GitHub repo.
  python scripts/update_clippy.py check   -> asks GitHub for the newest version, writes update_state.json
  python scripts/update_clippy.py apply   -> downloads it and replaces ONLY program files (.py/.html/.bat in the main folder and scripts/)
Never touched: your settings, keys, accounts, clips, review queue, logs, history, downloads (they are not program files).
Replaced files are first copied to update_backup/<time>/. Only talks to api.github.com and codeload.github.com."""
import datetime as dt, io, json, os, pathlib, re, shutil, sys, urllib.request, zipfile

os.environ["CLIPPY_ALLOW_UPDATE"] = "1"  # netguard opens just the two GitHub hosts for this process
R = pathlib.Path(__file__).resolve().parent.parent
REPO = "FrostyDog222/Clippy"
STATE, VER = R / "update_state.json", R / "version.json"
EXT = {".py", ".html", ".bat"}


def get(url, limit=30_000_000):
    rq = urllib.request.Request(url, headers={"User-Agent": "Clippy-updater"})
    with urllib.request.urlopen(rq, timeout=60) as r:
        data = r.read(limit + 1)
    assert len(data) <= limit, "download too large"
    return data


def installed():
    try:
        return json.loads(VER.read_text(encoding="utf-8")).get("sha", "")
    except Exception:
        return ""


def save(**kw):
    try:
        st = json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        st = {}
    st.update(kw); STATE.write_text(json.dumps(st), encoding="utf-8")


def latest():
    j = json.loads(get(f"https://api.github.com/repos/{REPO}/commits/main", 2_000_000))
    sha = j["sha"]; assert re.fullmatch(r"[0-9a-f]{40}", sha)
    return sha, (j.get("commit", {}).get("message", "").splitlines() or [""])[0][:120]


def check():
    if (R / ".no_update").exists():
        return save(error="", available=False, note="updates are switched off for this copy (.no_update)")
    try:
        sha, msg = latest()
        try:
            skip = json.loads(STATE.read_text(encoding="utf-8")).get("skip")
        except Exception:
            skip = None
        save(checked=dt.datetime.now().isoformat(timespec="seconds"), latest=sha, message=msg, available=sha != installed() and sha != skip, error="", note="")  # a version you went back from is not offered again
    except Exception as e:
        save(checked=dt.datetime.now().isoformat(timespec="seconds"), error=f"Could not check for updates: {str(e)[:120]}")


def apply():
    if (R / ".no_update").exists():
        return save(error="updates are switched off for this copy (.no_update)")
    try:
        sha, msg = latest()
        z = zipfile.ZipFile(io.BytesIO(get(f"https://codeload.github.com/{REPO}/zip/{sha}")))
        bak = R / "update_backup" / dt.datetime.now().strftime("%Y%m%d-%H%M%S"); n = 0; prev = installed()
        for info in z.infolist():
            parts = info.filename.split("/")[1:]  # drop the "Clippy-<sha>/" folder
            if info.is_dir() or not parts or len(parts) > 2 or (len(parts) == 2 and parts[0] != "scripts"):
                continue
            if any(p in ("", ".", "..") or ":" in p or "\\" in p for p in parts) or pathlib.PurePath(parts[-1]).suffix.lower() not in EXT:
                continue
            dst = R.joinpath(*parts); data = z.read(info)
            if dst.exists():
                if dst.read_bytes() == data:
                    continue
                (bak / pathlib.Path(*parts)).parent.mkdir(parents=True, exist_ok=True); shutil.copy2(dst, bak / pathlib.Path(*parts))
            dst.parent.mkdir(exist_ok=True); tmp = dst.with_name(dst.name + ".new"); tmp.write_bytes(data); os.replace(tmp, dst); n += 1
        if n and bak.exists():
            (bak / "_prev.json").write_text(json.dumps({"sha": prev, "new": sha}), encoding="utf-8")  # what "go back" returns to
        VER.write_text(json.dumps({"sha": sha}), encoding="utf-8")
        save(installed=sha, latest=sha, available=False, applied=dt.datetime.now().isoformat(timespec="seconds"), files=n, restart=n > 0, message=msg, error="")
    except Exception as e:
        save(error=f"Update failed (nothing was lost): {str(e)[:120]}")


def backups():
    """Update backups that can still be used to go back, newest first."""
    d = R / "update_backup"
    return sorted((p for p in d.iterdir() if p.is_dir() and not p.name.endswith(".used") and (p / "_prev.json").exists()), reverse=True) if d.exists() else []


def rollback():
    """Go back to the version before the last update: copy the program files saved by that update back. Settings, clips and keys are not touched."""
    try:
        b = backups()
        if not b:
            return save(error="There is no earlier version to go back to.")
        bak, n = b[0], 0
        info = json.loads((bak / "_prev.json").read_text(encoding="utf-8"))
        for f in bak.rglob("*"):
            rel = f.relative_to(bak).parts
            if not f.is_file() or f.name == "_prev.json" or len(rel) > 2 or (len(rel) == 2 and rel[0] != "scripts") or f.suffix.lower() not in EXT:
                continue
            dst = R.joinpath(*rel); tmp = dst.with_name(dst.name + ".new"); tmp.write_bytes(f.read_bytes()); os.replace(tmp, dst); n += 1
        if info.get("sha"):
            VER.write_text(json.dumps({"sha": info["sha"]}), encoding="utf-8")
        else:
            VER.unlink(missing_ok=True)
        bak.rename(bak.with_name(bak.name + ".used"))
        save(installed=info.get("sha", ""), available=False, skip=info.get("new"), applied=dt.datetime.now().isoformat(timespec="seconds"), files=n, restart=n > 0,
             message="Went back to the previous version", error="")
    except Exception as e:
        save(error=f"Going back failed (nothing was lost): {str(e)[:120]}")


if __name__ == "__main__":
    {"check": check, "apply": apply, "rollback": rollback}[sys.argv[1] if len(sys.argv) > 1 else "check"]()
