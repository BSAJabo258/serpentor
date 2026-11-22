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
