"""Encrypted storage for API keys (Windows DPAPI, current user): api_tokens/<name>.dpapi.
The ciphertext can only be decrypted by the same Windows user on the same PC. Copy the folder to another PC or user (or send it to someone) and the keys are
useless noise. Keys are never written in plain text, never returned to the dashboard page, and never part of a release (make_release.py skips api_tokens/).
save(name, text) | load(name) -> str | exists(name) | delete(name) | migrate_all()  (turns old plain-text api_tokens/*.txt keys into encrypted files)"""
import ctypes, ctypes.wintypes as wt, pathlib, re

R = pathlib.Path(__file__).resolve().parent.parent
D = R / "api_tokens"
ENTROPY = b"clippy-api-key-v1"


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(b):
    buf = ctypes.create_string_buffer(b, len(b))
    return _Blob(len(b), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def _crypt(data, protect):
    inb, keep = _blob(data); ent, keep2 = _blob(ENTROPY); out = _Blob()
    f = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
    ok = f(ctypes.byref(inb), None, ctypes.byref(ent), None, None, 0, ctypes.byref(out)) if protect else \
        f(ctypes.byref(inb), None, ctypes.byref(ent), None, None, 0, ctypes.byref(out))
    if not ok:
        raise OSError("Windows could not " + ("encrypt" if protect else "decrypt") + " the key (different Windows user or PC?)")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def _path(name):
    assert re.match(r"^[A-Za-z0-9_\-]{1,40}$", name), "bad secret name"
    return D / f"{name}.dpapi"


def save(name, text):
    D.mkdir(exist_ok=True)
    _path(name).write_bytes(_crypt(text.encode("utf-8"), True))
    (D / f"{name}.txt").unlink(missing_ok=True)  # never leave a plain-text copy behind


def load(name):
    p = _path(name)
    if p.exists():
        try:
            return _crypt(p.read_bytes(), False).decode("utf-8")
        except OSError:
            return ""
    legacy = D / f"{name}.txt"  # an old plain-text key: encrypt it now
    if legacy.exists():
        t = legacy.read_text(encoding="utf-8").strip()
        if t:
            save(name, t)
        return t
    return ""


def exists(name):
    return _path(name).exists() or (D / f"{name}.txt").exists()


def delete(name):
    _path(name).unlink(missing_ok=True); (D / f"{name}.txt").unlink(missing_ok=True)


def migrate_all():
    n = 0
    for f in list(D.glob("key_*.txt")) + list(D.glob("title_api_key.txt")):
        t = f.read_text(encoding="utf-8").strip()
        if t:
            save(f.stem, t); n += 1
        else:
            f.unlink(missing_ok=True)
    return n


if __name__ == "__main__":
    print(f"encrypted {migrate_all()} plain-text key file(s)")
