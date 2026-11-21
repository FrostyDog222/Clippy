"""Installed as <venv>\\Lib\\site-packages\\sitecustomize.py (setup.bat copies it there). Python runs it at start-up in every process of this venv and it
switches on scripts/netguard.py (see that file). Fails open on any error so it can never stop Python from starting."""
try:
    import importlib.util, pathlib
    _root = pathlib.Path(__file__).resolve().parents[3]  # <project>\.venv\Lib\site-packages\sitecustomize.py -> <project>
    _f = _root / "scripts" / "netguard.py"
    if _f.exists():
        _spec = importlib.util.spec_from_file_location("clippy_netguard", _f)
        _m = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(_m); _m.install()
except Exception:
    pass

try:  # Windows: child processes (ffmpeg, python, yt-dlp...) must never flash a console window over the user's own work
    import os, subprocess
    if os.name == "nt":
        _orig = subprocess.Popen.__init__
        def _quiet(self, *a, **k):
            k["creationflags"] = k.get("creationflags", 0) | 0x08000000  # CREATE_NO_WINDOW
            _orig(self, *a, **k)
        subprocess.Popen.__init__ = _quiet
except Exception:
    pass
