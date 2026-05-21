import json
import logging
from io import BytesIO

from minio import Minio

logger = logging.getLogger(__name__)

BUCKET_PREDICCIONES  = "predicciones"   # predicciones/<guid>/audio.<ext> · transcripcion.txt · dataset.json
BUCKET_ENTRENAMIENTO = "entrenamiento"  # entrenamiento/texto · enriquecidos · datasets


class MinIOService:
    def __init__(self, endpoint: str, access_key: str, secret_key: str):
        self._client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=False)
        self._inicializar_buckets()

    def _inicializar_buckets(self):
        for b in (BUCKET_PREDICCIONES, BUCKET_ENTRENAMIENTO):
            try:
                if not self._client.bucket_exists(b):
                    self._client.make_bucket(b)
            except Exception:
                logger.exception("No se pudo inicializar bucket %s", b)

    # ------------------------------------------------------------------ #
    # LECTURA                                                             #
    # ------------------------------------------------------------------ #

    def descargar_json(self, guid: str) -> dict | None:
        try:
            data = self._client.get_object(BUCKET_PREDICCIONES, f"{guid}/dataset.json").read()
            return json.loads(data)
        except Exception:
            return None

    # ------------------------------------------------------------------ #
    # ESCRITURA                                                           #
    # ------------------------------------------------------------------ #

    def subir_bytes(self, bucket: str, nombre: str, datos: bytes, content_type: str = "application/octet-stream") -> str:
        self._client.put_object(bucket, nombre, BytesIO(datos), len(datos), content_type=content_type)
        return f"minio://{bucket}/{nombre}"

    def subir_audio(self, guid: str, datos: bytes, ext: str) -> str:
        return self.subir_bytes(
            BUCKET_PREDICCIONES, f"{guid}/audio.{ext}",
            datos, content_type=f"audio/{ext}",
        )

    def subir_texto(self, guid: str, texto: str) -> str:
        data = texto.encode("utf-8")
        return self.subir_bytes(
            BUCKET_PREDICCIONES, f"{guid}/transcripcion.txt",
            data, content_type="text/plain",
        )

    def subir_json(self, guid: str, datos: dict) -> str:
        data = json.dumps(datos, ensure_ascii=False).encode("utf-8")
        return self.subir_bytes(
            BUCKET_PREDICCIONES, f"{guid}/dataset.json",
            data, content_type="application/json",
        )
