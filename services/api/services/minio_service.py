import json
import logging
from io import BytesIO

from minio import Minio

logger = logging.getLogger(__name__)

BUCKET_ENRIQUECIDOS = "enriquecidos"
BUCKET_MODELOS      = "modelos"
BUCKET_DATASETS     = "datasets"


class MinIOService:
    def __init__(self, endpoint: str, access_key: str, secret_key: str):
        self._client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=False)

    def descargar_json(self, guid: str) -> dict | None:
        try:
            data = self._client.get_object(BUCKET_ENRIQUECIDOS, f"{guid}.json").read()
            return json.loads(data)
        except Exception:
            return None

    def descargar_modelo(self) -> bytes | None:
        try:
            return self._client.get_object(BUCKET_MODELOS, "modelo_triageia.pkl").read()
        except Exception:
            return None

    def descargar_imagen(self, nombre: str) -> bytes | None:
        try:
            return self._client.get_object(BUCKET_MODELOS, nombre).read()
        except Exception:
            return None

    def subir_bytes(self, bucket: str, nombre: str, datos: bytes, content_type: str = "application/octet-stream") -> str:
        self._client.put_object(bucket, nombre, BytesIO(datos), len(datos), content_type=content_type)
        return f"minio://{bucket}/{nombre}"
