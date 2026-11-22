"""Runtime configuration. Everything is overridable by environment variables."""
import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("BSI_DATA_DIR", "./data")).resolve()
VAULT_DIR = DATA_DIR / "vault"          # write-once originals, named by SHA-256
DATABASE_URL = os.environ.get("BSI_DATABASE_URL", f"sqlite:///{DATA_DIR / 'bsi.db'}")

SEARXNG_URL = os.environ.get("BSI_SEARXNG_URL", "http://localhost:8888")
OLLAMA_URL = os.environ.get("BSI_OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("BSI_OLLAMA_MODEL", "qwen2.5:7b-instruct")

# Hosts the system may send SENSITIVE material to. Anything else is refused.
LOCAL_HOSTS = {h.strip() for h in os.environ.get(
    "BSI_LOCAL_HOSTS", "localhost,127.0.0.1,ollama,searxng,postgres").split(",")}
