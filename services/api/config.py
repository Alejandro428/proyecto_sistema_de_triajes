import os
import sys


def _require(name: str) -> str:
    val = os.getenv(name)
    if not val:
        print(f"ERROR: variable de entorno requerida: {name}", file=sys.stderr)
        sys.exit(1)
    return val


DATABASE_URL     = _require("DATABASE_URL")

MINIO_ENDPOINT   = os.getenv("MINIO_ENDPOINT",   "minio:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY",  "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY",  "minioadmin")

MISTRAL_API_KEY  = _require("MISTRAL_API_KEY")
