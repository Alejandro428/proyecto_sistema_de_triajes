import os
import sys


def _require(name: str) -> str:
    val = os.getenv(name)
    if not val:
        print(f"ERROR: variable de entorno requerida no configurada: {name}", file=sys.stderr)
        sys.exit(1)
    return val


# Anthropic
ANTHROPIC_API_KEY = _require("ANTHROPIC_API_KEY")

# PostgreSQL
DATABASE_URL = _require("DATABASE_URL")

# Servicios externos
WHISPER_URL      = os.getenv("WHISPER_URL", "http://whisper:8001")
MLFLOW_TRACKING_URI = os.getenv("MLFLOW_TRACKING_URI", "http://mlflow:5000")
