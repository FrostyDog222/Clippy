"""Report run progress for the dashboard bar. Usage: python scripts/progress.py <0-100> "<message>"   (100 = finished, -1 = failed)"""
import datetime as dt, json, pathlib, sys

F = pathlib.Path(__file__).resolve().parent.parent / "progress.json"
pct, msg = int(sys.argv[1]), (sys.argv[2] if len(sys.argv) > 2 else "")
F.write_text(json.dumps({"pct": pct, "msg": msg, "ts": dt.datetime.now().isoformat(timespec="seconds")}), encoding="utf-8")
