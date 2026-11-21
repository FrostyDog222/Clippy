"""Official-API uploader (YouTube Data API v3 + TikTok Content Posting API). UNTESTED until you add your own developer credentials.

WARNINGS (also shown in the dashboard):
 * YouTube: API projects created after 2020-07-28 that are not audited by Google upload videos as PRIVATE only (the API silently locks them).
   Requesting public will not make it public. Apply for the audit: https://support.google.com/youtube/contact/yt_api_form
 * YouTube quota: default 10,000 units/day, one upload costs 1,600 -> at most 6 uploads/day, and nothing else can use the quota.
 * TikTok: unaudited apps can only post with privacy SELF_ONLY ("Only me") and to a limited number of private test accounts.
   Public posting needs TikTok to audit your app (can take weeks, may be refused). Setup is more involved than YouTube.
 * Both need developer accounts you create yourself. Your client secrets stay in api_tokens\\ on this PC; never share that folder.

Setup (one-time):
 YouTube: Google Cloud console -> new project -> enable "YouTube Data API v3" -> OAuth consent screen (External, add yourself as test user)
          -> Credentials -> OAuth client ID type "Desktop app" -> download JSON -> save as api_tokens\\youtube_client_secret.json
 TikTok:  developers.tiktok.com -> create app -> add product "Content Posting API" (Direct Post) + Login Kit, scope video.publish
          -> register redirect URI (e.g. http://127.0.0.1:8777/callback) -> save api_tokens\\tiktok_app.json as
          {"client_key": "...", "client_secret": "...", "redirect_uri": "http://127.0.0.1:8777/callback"}
Usage:
  python scripts/api_upload.py auth youtube|tiktok          (opens your browser once; you log in and approve yourself)
  python scripts/api_upload.py post <clip.mp4> --title T --description D --caption C [--mode private|public] [--only youtube|tiktok]
Exit codes: 0 ok, 2 bad args/setup missing, 5 failure. Prints one JSON line like upload.py."""
import argparse, http.server, json, pathlib, sys, threading, time, urllib.parse

import requests

R = pathlib.Path(__file__).resolve().parent.parent
T = R / "api_tokens"; T.mkdir(exist_ok=True)
YT_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


# ---------- YouTube ----------
def yt_creds():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    tok = T / "youtube_token.json"
    creds = Credentials.from_authorized_user_file(str(tok), YT_SCOPES) if tok.exists() else None
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request()); tok.write_text(creds.to_json())
    if not creds or not creds.valid:
        raise SystemExit("YouTube not authorised. Run: api_upload.py auth youtube")
    return creds


def yt_auth():
    from google_auth_oauthlib.flow import InstalledAppFlow
    sec = T / "youtube_client_secret.json"
    if not sec.exists():
        sys.exit(f"Missing {sec} (see the setup steps at the top of this file)")
    creds = InstalledAppFlow.from_client_secrets_file(str(sec), YT_SCOPES).run_local_server(port=0)
    (T / "youtube_token.json").write_text(creds.to_json()); print("YouTube authorised")


def yt_post(clip, title, desc, public):
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    yt = build("youtube", "v3", credentials=yt_creds())
    body = {"snippet": {"title": title[:100], "description": desc, "categoryId": "20"},
            "status": {"privacyStatus": "public" if public else "private", "selfDeclaredMadeForKids": False}}
    req = yt.videos().insert(part="snippet,status", body=body, media_body=MediaFileUpload(str(clip), chunksize=8 * 1024 * 1024, resumable=True))
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    vid = resp["id"]
    note = "" if not public else " (if this shows Private, your API project is unaudited: YouTube forces Private)"
    return f"https://youtube.com/shorts/{vid}{note}"


# ---------- TikTok ----------
API = "https://open.tiktokapis.com"


def tt_app():
    f = T / "tiktok_app.json"
    if not f.exists():
        sys.exit(f"Missing {f} (see the setup steps at the top of this file)")
    return json.loads(f.read_text())


def tt_auth():
    app = tt_app(); got = {}
    port = urllib.parse.urlparse(app["redirect_uri"]).port or 8777

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query); got.update({k: v[0] for k, v in q.items()})
            self.send_response(200); self.end_headers(); self.wfile.write(b"TikTok authorised. You can close this tab.")
        def log_message(self, *a): pass
    srv = http.server.HTTPServer(("127.0.0.1", port), H)
    url = "https://www.tiktok.com/v2/auth/authorize/?" + urllib.parse.urlencode({
        "client_key": app["client_key"], "scope": "video.publish,video.upload", "response_type": "code",
        "redirect_uri": app["redirect_uri"], "state": "autopost"})
    import webbrowser; webbrowser.open(url); print("Approve in the browser tab that opened...")
    while "code" not in got:
        srv.handle_request()
    r = requests.post(f"{API}/v2/oauth/token/", data={"client_key": app["client_key"], "client_secret": app["client_secret"],
                      "code": got["code"], "grant_type": "authorization_code", "redirect_uri": app["redirect_uri"]}, timeout=30).json()
    if "access_token" not in r:
        sys.exit(f"TikTok auth failed: {r}")
    r["obtained"] = int(time.time()); (T / "tiktok_token.json").write_text(json.dumps(r)); print("TikTok authorised")


def tt_token():
    f = T / "tiktok_token.json"
    if not f.exists():
        raise SystemExit("TikTok not authorised. Run: api_upload.py auth tiktok")
    tok = json.loads(f.read_text())
    if time.time() > tok["obtained"] + int(tok.get("expires_in", 86400)) - 300:
        app = tt_app()
        r = requests.post(f"{API}/v2/oauth/token/", data={"client_key": app["client_key"], "client_secret": app["client_secret"],
                          "grant_type": "refresh_token", "refresh_token": tok["refresh_token"]}, timeout=30).json()
        if "access_token" not in r:
            raise SystemExit(f"TikTok token refresh failed: {r}")
        r["obtained"] = int(time.time()); f.write_text(json.dumps(r)); tok = r
    return tok["access_token"]


def tt_post(clip, caption, public):
    tok = tt_token(); size = clip.stat().st_size
    chunk = size if size < 5 * 1024 * 1024 else 10 * 1024 * 1024
    total = max(1, size // chunk)
    H = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json; charset=UTF-8"}
    init = requests.post(f"{API}/v2/post/publish/video/init/", headers=H, timeout=30, json={
        "post_info": {"title": caption[:150], "privacy_level": "PUBLIC_TO_EVERYONE" if public else "SELF_ONLY"},
        "source_info": {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk, "total_chunk_count": total}}).json()
    if init.get("error", {}).get("code") not in (None, "ok"):
        raise RuntimeError(f"TikTok init: {init['error']}")
    up, pid = init["data"]["upload_url"], init["data"]["publish_id"]
    with open(clip, "rb") as f:
        for i in range(total):
            start = i * chunk; end = size - 1 if i == total - 1 else start + chunk - 1
            f.seek(start); data = f.read(end - start + 1)
            r = requests.put(up, data=data, timeout=300, headers={"Content-Type": "video/mp4", "Content-Length": str(len(data)),
                                                                  "Content-Range": f"bytes {start}-{end}/{size}"})
            if r.status_code not in (200, 201, 206):
                raise RuntimeError(f"TikTok chunk {i}: HTTP {r.status_code} {r.text[:200]}")
    for _ in range(30):
        s = requests.post(f"{API}/v2/post/publish/status/fetch/", headers=H, json={"publish_id": pid}, timeout=30).json()
        st = s.get("data", {}).get("status")
        if st in ("PUBLISH_COMPLETE", "SEND_TO_USER_INBOX"):
            return st.lower() + ("" if public else " (Only me: unaudited TikTok apps cannot post public)")
        if st == "FAILED":
            raise RuntimeError(f"TikTok failed: {s}")
        time.sleep(5)
    return "processing (check TikTok)"


def main():
    ap = argparse.ArgumentParser(); sp = ap.add_subparsers(dest="cmd", required=True)
    au = sp.add_parser("auth"); au.add_argument("which", choices=["youtube", "tiktok"])
    q = sp.add_parser("post"); q.add_argument("clip"); q.add_argument("--title", required=True); q.add_argument("--description", required=True)
    q.add_argument("--caption", required=True); q.add_argument("--mode", choices=["private", "public"], default="private"); q.add_argument("--only", choices=["youtube", "tiktok"])
    a = ap.parse_args()
    if a.cmd == "auth":
        (yt_auth if a.which == "youtube" else tt_auth)(); return 0
    clip = pathlib.Path(a.clip).resolve(); res = {"youtube": None, "tiktok": None, "error": None}; code = 0
    if not clip.exists():
        print(json.dumps({"error": "clip not found"})); return 2
    pub = a.mode == "public"
    for name, fn in (("youtube", lambda: yt_post(clip, a.title, a.description, pub)), ("tiktok", lambda: tt_post(clip, a.caption, pub))):
        if a.only and a.only != name:
            continue
        try:
            res[name] = fn()
        except SystemExit as e:
            res["error"] = f"{name}: {e}"; code = 2; break
        except Exception as e:
            res["error"] = f"{name}: {type(e).__name__}: {str(e)[:300]}"; code = 5; break
    print(json.dumps(res)); return code


if __name__ == "__main__":
    sys.exit(main())
