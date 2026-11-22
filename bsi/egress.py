"""Outbound policy. Sensitive material may only go to local hosts; every outbound
call is checked here and recorded in the audit chain."""
from urllib.parse import urlparse

from . import config


class EgressDenied(Exception):
    pass


def check(url: str, *, sensitive: bool) -> None:
    host = urlparse(url).hostname or ""
    if sensitive and host not in config.LOCAL_HOSTS:
        raise EgressDenied(f"sensitive content may not be sent to {host}")


def check_public(url: str, resolve=None) -> None:
    """For fetching user-supplied URLs (archive imports): only http(s) to public internet
    addresses. Blocks loopback, private, link-local and reserved ranges so an import can't
    be pointed at services on this machine or the local network. Checked on every redirect."""
    import ipaddress
    import socket
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise EgressDenied("only http(s) URLs can be imported")
    resolve = resolve or socket.getaddrinfo
    try:
        infos = resolve(p.hostname, p.port or (443 if p.scheme == "https" else 80))
    except OSError as e:
        raise EgressDenied(f"cannot resolve {p.hostname}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global or ip.is_multicast:
            raise EgressDenied(f"{p.hostname} resolves to a non-public address")
