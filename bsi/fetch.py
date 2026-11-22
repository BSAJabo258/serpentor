"""Bring an archive item into the evidence vault: download, hash, extract, audit."""
import tempfile
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

from . import egress, vault
from .connectors.base import USER_AGENT

MAX_BYTES = 250 * 1024 * 1024
MAX_REDIRECTS = 5
EXT = {"application/pdf": ".pdf", "text/plain": ".txt", "text/html": ".html",
       "image/jpeg": ".jpg", "image/png": ".png", "image/tiff": ".tif", "application/json": ".json",
       "application/xml": ".xml", "text/xml": ".xml", "text/csv": ".csv"}
KNOWN_SUFFIXES = set(EXT.values()) | {".htm", ".md", ".jpeg", ".tiff"}


def _suffix(url: str, ctype: str) -> str:
    """Prefer a recognized Content-Type over a misleading URL suffix (download.php -> .pdf)."""
    if ctype in EXT:
        return EXT[ctype]
    suf = Path(urlparse(url).path).suffix.lower()
    return suf if suf in KNOWN_SUFFIXES else ".bin"


def fetch_to_vault(url: str, *, title=None, tier="unverified", holder=None, created_date=None,
                   created_year=None, client: httpx.Client | None = None, resolve=None):
    c = client or httpx.Client(timeout=120, headers={"User-Agent": USER_AGENT})
    for _ in range(MAX_REDIRECTS + 1):
        addrs = egress.check_public(url, resolve=resolve)     # re-checked on every hop
        target, headers, ext = egress.pinned(url, addrs[0])   # connect to the address we validated
        with tempfile.TemporaryDirectory() as d:
            with c.stream("GET", target, headers=headers, extensions=ext, follow_redirects=False) as r:
                if r.is_redirect:
                    url = urljoin(url, r.headers["location"])
                    continue
                r.raise_for_status()
                ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
                p = Path(d) / f"download{_suffix(url, ctype)}"
                size = 0
                with open(p, "wb") as f:
                    for chunk in r.iter_bytes():
                        size += len(chunk)
                        if size > MAX_BYTES:
                            raise ValueError("file larger than 250 MB — download manually and ingest")
                        f.write(chunk)
            return vault.register(p, title=title or Path(urlparse(url).path).name or url, tier=tier,
                                  holder=holder, created_date=created_date, created_year=created_year,
                                  origin_url=url)
    raise ValueError("too many redirects")
