"""Bring an archive item into the evidence vault: download, hash, extract, audit."""
import os
import tempfile
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

from . import egress, vault
from .connectors.base import USER_AGENT

MAX_BYTES = 250 * 1024 * 1024
MAX_REDIRECTS = 5
# Ask for the bytes as stored. A compressed transfer (gzip, gzip, …) could inflate far past
# MAX_BYTES before any size check, so encoded bodies are refused and raw bytes are capped.
NO_ENCODING = {"Accept-Encoding": "identity"}
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


def _proxy_for(url: str) -> str | None:
    """The proxy URL the environment would use for this URL, or None for a direct connection.

    This is the single routing decision: the clients themselves never read proxy settings
    (trust_env=False), so httpx can't route a hop differently from what we checked. Matches
    HTTP(S)_PROXY and ALL_PROXY; NO_PROXY is checked against both "host" and "host:port"."""
    import urllib.request
    u = urlparse(url)
    env = urllib.request.getproxies()
    proxy = env.get(u.scheme) or env.get("all")
    if not proxy:
        return None
    host = u.hostname or ""
    port = u.port or (443 if u.scheme == "https" else 80)
    if urllib.request.proxy_bypass(host) or urllib.request.proxy_bypass(f"{host}:{port}"):
        return None
    return proxy


def _proxied(url: str) -> bool:
    """True when the environment routes this URL through a proxy."""
    return _proxy_for(url) is not None


def _allow_proxied() -> bool:
    return os.environ.get("BSI_ALLOW_PROXIED_IMPORTS", "").lower() in ("1", "true", "yes")


def _open(c: httpx.Client, url: str, addrs: list[str], proxied: bool) -> httpx.Response:
    """Open a streaming response to a validated address.

    Direct: connect to each validated IP in turn (pinned; Host + SNI keep the hostname so
    TLS verifies the real certificate), moving on only when the connection can't be made.
    Behind a proxy: the proxy does the DNS lookup and httpcore verifies TLS against the
    connect host, so pinning to an IP would break certificate checks; we send the hostname
    and rely on the pre-check (documented residual risk for proxied setups)."""
    if proxied:
        return c.send(c.build_request("GET", url, headers=NO_ENCODING), stream=True, follow_redirects=False)
    last = None
    for ip in addrs:
        target, headers, ext = egress.pinned(url, ip)
        headers = {**headers, **NO_ENCODING}
        try:
            return c.send(c.build_request("GET", target, headers=headers, extensions=ext),
                          stream=True, follow_redirects=False)
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            last = e
    raise last


def fetch_to_vault(url: str, *, title=None, tier="unverified", holder=None, created_date=None,
                   created_year=None, rights=None, client: httpx.Client | None = None, resolve=None,
                   proxied: bool | None = None):
    own = client is None
    # Our own direct client ignores proxy environment variables entirely, so a pinned-IP request
    # is never routed through a proxy (httpx matches NO_PROXY against the request URL, which is
    # the IP, not the hostname). A separate proxy-aware client is opened only for a hop that
    # really goes through the proxy, and only when proxied imports are opted in.
    c = client or httpx.Client(timeout=120, headers={"User-Agent": USER_AGENT}, trust_env=False)
    pc = None
    try:
        for _ in range(MAX_REDIRECTS + 1):
            addrs = egress.check_public(url, resolve=resolve)          # re-checked on every hop
            via_proxy = _proxied(url) if proxied is None and own else bool(proxied)
            if via_proxy and not _allow_proxied():
                # Behind a proxy, the proxy resolves the hostname, so our public-address check
                # can't be enforced at connect time. Refuse unless the operator opts in (e.g. a
                # proxy that itself blocks private destinations).
                raise egress.EgressDenied("imports through an HTTP(S) proxy are disabled; set "
                                          "BSI_ALLOW_PROXIED_IMPORTS=1 if your proxy blocks private addresses")
            with tempfile.TemporaryDirectory() as d:
                if via_proxy and own:
                    # explicit proxy, env ignored: httpx can't apply its own NO_PROXY matching
                    # and silently send this hop direct with an unpinned hostname
                    if pc is not None:
                        pc.close()
                    pc = httpx.Client(timeout=120, headers={"User-Agent": USER_AGENT},
                                      proxy=_proxy_for(url), trust_env=False)
                r = _open(pc if (via_proxy and own) else c, url, addrs, via_proxy)
                try:
                    if r.is_redirect:
                        url = urljoin(url, r.headers["location"])
                        continue
                    r.raise_for_status()
                    enc = r.headers.get("content-encoding", "identity").strip().lower()
                    if enc not in ("", "identity"):
                        raise ValueError(f"refused compressed transfer ({enc}) — download manually and ingest")
                    ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
                    p = Path(d) / f"download{_suffix(url, ctype)}"
                    size = 0
                    with open(p, "wb") as f:
                        for chunk in r.iter_raw():         # no decoding: the cap applies to real bytes
                            size += len(chunk)
                            if size > MAX_BYTES:
                                raise ValueError("file larger than 250 MB — download manually and ingest")
                            f.write(chunk)
                finally:
                    r.close()
                return vault.register(p, title=title or Path(urlparse(url).path).name or url, tier=tier,
                                      holder=holder, created_date=created_date, created_year=created_year,
                                      origin_url=url, rights=rights)
        raise ValueError("too many redirects")
    finally:
        if own:
            c.close()
            if pc is not None:
                pc.close()
