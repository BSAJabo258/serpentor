"""Runtime configuration. Everything is overridable by environment variables."""
import os
from pathlib import Path


def _load_dotenv(path=Path(".env")):
    """KEY=VALUE lines from ./.env; real environment variables always win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            if v.strip():
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()

DATA_DIR = Path(os.environ.get("BSI_DATA_DIR", "./data")).resolve()
VAULT_DIR = DATA_DIR / "vault"          # write-once originals, named by SHA-256
DATABASE_URL = os.environ.get("BSI_DATABASE_URL", f"sqlite:///{DATA_DIR / 'bsi.db'}")

SEARXNG_URL = os.environ.get("BSI_SEARXNG_URL", "http://localhost:8888")
OLLAMA_URL = os.environ.get("BSI_OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("BSI_OLLAMA_MODEL", "qwen2.5:7b-instruct")

# Hosts the system may send SENSITIVE material to. Anything else is refused.
LOCAL_HOSTS = {h.strip() for h in os.environ.get(
    "BSI_LOCAL_HOSTS", "localhost,127.0.0.1,ollama,searxng,postgres").split(",")}
