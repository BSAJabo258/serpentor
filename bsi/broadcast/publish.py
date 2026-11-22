"""Publishers. Only called for episodes a person has approved.

YouTube (implemented): uploads via the YouTube Data API with
  - privacyStatus "private" by default (change to public in Studio after a final look,
    or pass publish_at to schedule). Note: uploads from an UNVERIFIED Google Cloud
    project are forced private until the project passes Google's API audit.
  - status.containsSyntheticMedia = True whenever the narration is synthetic.
  - captions.srt uploaded as the caption track, thumbnail set.
Setup once: create an OAuth "Desktop app" client in Google Cloud, enable YouTube Data
API v3, save the JSON as BSI_YOUTUBE_CLIENT_SECRETS; first run opens a browser to sign in.

Instagram / TikTok / Facebook: the episode folder already contains the Reel/Short
(short.mp4), carousel slides and caption. Their APIs need a business app, a public
media URL (Instagram) or an audited client (TikTok posts stay private until TikTok
audits the app), so they're packaged for upload rather than auto-posted here.
Instagram's API supports an is_ai_generated flag — set it if you automate later.
"""
import json
import os
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.force-ssl"]


def _youtube():
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as e:
        raise RuntimeError("pip install google-api-python-client google-auth-oauthlib") from e
    secrets = os.environ.get("BSI_YOUTUBE_CLIENT_SECRETS")
    if not secrets:
        raise RuntimeError("set BSI_YOUTUBE_CLIENT_SECRETS to your OAuth client JSON")
    token = Path(os.environ.get("BSI_YOUTUBE_TOKEN", Path(secrets).with_name("youtube_token.json")))
    creds = Credentials.from_authorized_user_file(str(token), SCOPES) if token.exists() else None
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            creds = InstalledAppFlow.from_client_secrets_file(secrets, SCOPES).run_local_server(port=0)
        token.write_text(creds.to_json())
    return build("youtube", "v3", credentials=creds)


def youtube(files: dict, script: dict, title: str, *, privacy: str = "private", publish_at: str | None = None,
            short: bool = False, client=None) -> dict:
    from googleapiclient.http import MediaFileUpload
    yt = client or _youtube()
    manifest = json.loads(Path(files["manifest"]).read_text())
    desc = Path(files["description"]).read_text()
    status = {"privacyStatus": "private" if publish_at else privacy, "selfDeclaredMadeForKids": False,
              "containsSyntheticMedia": bool(manifest.get("synthetic_voice"))}
    if publish_at:
        status["publishAt"] = publish_at
    body = {"snippet": {"title": (title + (" #Shorts" if short else ""))[:100], "description": desc[:4900],
                        "categoryId": "27",  # Education
                        "tags": ["history", "primary sources", "archives", "documentary"]},
            "status": status}
    video = files["short" if short else "episode"]
    req = yt.videos().insert(part="snippet,status", body=body,
                             media_body=MediaFileUpload(video, chunksize=8 << 20, resumable=True))
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    vid = resp["id"]
    if not short:
        try:
            yt.thumbnails().set(videoId=vid, media_body=MediaFileUpload(files["thumbnail"])).execute()
        except Exception:
            pass   # custom thumbnails need a verified channel
        yt.captions().insert(part="snippet", body={"snippet": {"videoId": vid, "language": "en", "name": "English"}},
                             media_body=MediaFileUpload(files["captions"])).execute()
    return {"video_id": vid, "url": f"https://youtu.be/{vid}", "privacy": status["privacyStatus"],
            "contains_synthetic_media": status["containsSyntheticMedia"], "short": short}


def package(files: dict, script: dict, title: str, **_kw) -> dict:
    """Social package: everything to post by hand or through a scheduler (Buffer, Postiz, Metricool)."""
    return {"reel_or_short": files.get("short"), "carousel": sorted(v for k, v in files.items() if k.startswith("carousel")),
            "caption": Path(files["post"]).read_text() if files.get("post") else "",
            "note": "Upload as Reel/Short/TikTok + carousel post. Mark as AI-generated where the platform asks."}


PUBLISHERS = {"youtube": youtube, "package": package}
