"""Archive connectors.

Every connector returns the same Record shape, reports its own status (ok /
needs_key / error) instead of failing the whole run, and every run is saved
as a hash-identified manifest. When one archive is down or refuses, the same
item is often held elsewhere — that's why a query fans out to all of them.

Only official, documented public APIs are used. Keys are free and go in env vars.
"""
from .base import Connector, Record, REGISTRY, run_all, get  # noqa: F401
from . import sources  # noqa: F401  (registers connectors)
