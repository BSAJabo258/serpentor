"""Bring an archive item into the evidence vault: download, hash, extract, audit."""
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import httpx

from . import egress, vault
from .connectors.base import USER_AGENT

MAX_BYTES = 250 * 1024 * 1024
EXT = {"application/pdf": ".pdf", "text/plain": ".txt", "text/html": ".html",
       "image/jpeg": ".jpg", "image/png": ".png", "image/tiff": ".tif", "application/json": ".json"}


def fetch_to_vault(url: str, *, title=None, tier="unverified", holder=None, created_date=None,
                   created_year=None, client: httpx.Client | None = None):
    egress.check(url, sensitive=False)
    c = client or httpx.Client(timeout=120, follow_redirects=True, headers={"User-Agent": USER_AGENT})
    with tempfile.TemporaryDirectory() as d:
        with c.stream("GET", url) as r:
            r.raise_for_status()
            ctype = r.headers.get("content-type", "").split(";")[0].strip()
            suffix = Path(urlparse(url).path).suffix.lower() or EXT.get(ctype, ".bin")
            p = Path(d) / f"download{suffix}"
            size = 0
            with open(p, "wb") as f:
                for chunk in r.iter_bytes():
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise ValueError("file larger than 250 MB — download manually and ingest")
                    f.write(chunk)
        return vault.register(p, title=title or Path(urlparse(url).path).name, tier=tier,
                              holder=holder, created_date=created_date, created_year=created_year,
                              origin_url=url)
