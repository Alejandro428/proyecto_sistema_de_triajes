"""
Cliente MinIO para los DAGs de Airflow.

Estructura de buckets (alineada con el resto del sistema):

  predicciones/                              ← Fase 3 (no usado por DAGs)
    <guid>/audio.<ext>
    <guid>/transcripcion.txt
    <guid>/dataset.json

  entrenamiento/                             ← Fase 1 (DAGs)
    texto/<guid>.txt
    enriquecidos/<guid>.json
    datasets/<filename>.csv

Las firmas públicas (subir_texto, subir_json, descargar_json, json_existe,
subir_bytes, descargar_bytes, BUCKET_DATASETS) se mantienen para no obligar
a tocar los DAGs más de lo necesario.
"""

import json
import logging
import os
from io import BytesIO

from minio import Minio

logger = logging.getLogger(__name__)

# Buckets (única fuente de verdad)
BUCKET_PREDICCIONES  = "predicciones"
BUCKET_ENTRENAMIENTO = "entrenamiento"

# Alias retrocompatible: los DAGs siguen importando BUCKET_DATASETS,
# pero ahora apunta al bucket de entrenamiento. Los DAGs deben usar
# nombres con prefijo "datasets/..." al subir/descargar CSVs.
BUCKET_DATASETS = BUCKET_ENTRENAMIENTO

# Prefijos dentro del bucket entrenamiento
_PREFIX_TEXTO        = "texto"
_PREFIX_ENRIQUECIDOS = "enriquecidos"

_BUCKETS = [BUCKET_PREDICCIONES, BUCKET_ENTRENAMIENTO]

_client: Minio | None = None


def get_client() -> Minio:
    global _client
    if _client is None:
        _client = Minio(
            os.environ.get("MINIO_ENDPOINT",   "minio:9000"),
            access_key=os.environ.get("MINIO_ACCESS_KEY", "minioadmin"),
            secret_key=os.environ.get("MINIO_SECRET_KEY", "minioadmin"),
            secure=False,
        )
        _inicializar_buckets(_client)
    return _client


def _inicializar_buckets(client: Minio) -> None:
    for bucket in _BUCKETS:
        if not client.bucket_exists(bucket):
            client.make_bucket(bucket)
            logger.info("Bucket creado: %s", bucket)


# ------------------------------------------------------------------ #
# Operaciones de texto (entrenamiento)                                 #
# ------------------------------------------------------------------ #

def subir_texto(guid: str, contenido: str) -> str:
    client = get_client()
    nombre = f"{_PREFIX_TEXTO}/{guid}.txt"
    data   = contenido.encode("utf-8")
    client.put_object(BUCKET_ENTRENAMIENTO, nombre, BytesIO(data), len(data), content_type="text/plain")
    url = f"minio://{BUCKET_ENTRENAMIENTO}/{nombre}"
    logger.debug("Texto subido: %s", url)
    return url


def descargar_texto(url: str) -> str:
    partes  = url.replace("minio://", "").split("/", 1)
    bucket, nombre = partes[0], partes[1]
    return get_client().get_object(bucket, nombre).read().decode("utf-8")


# ------------------------------------------------------------------ #
# Operaciones de JSON enriquecido (entrenamiento)                      #
# ------------------------------------------------------------------ #

def subir_json(guid: str, datos: dict) -> str:
    client = get_client()
    nombre = f"{_PREFIX_ENRIQUECIDOS}/{guid}.json"
    data   = json.dumps(datos, ensure_ascii=False).encode("utf-8")
    client.put_object(BUCKET_ENTRENAMIENTO, nombre, BytesIO(data), len(data), content_type="application/json")
    url = f"minio://{BUCKET_ENTRENAMIENTO}/{nombre}"
    logger.debug("JSON subido: %s", url)
    return url


def descargar_json(guid: str) -> dict:
    nombre = f"{_PREFIX_ENRIQUECIDOS}/{guid}.json"
    data   = get_client().get_object(BUCKET_ENTRENAMIENTO, nombre).read()
    return json.loads(data)


def json_existe(guid: str) -> bool:
    try:
        get_client().stat_object(BUCKET_ENTRENAMIENTO, f"{_PREFIX_ENRIQUECIDOS}/{guid}.json")
        return True
    except Exception:
        return False


# ------------------------------------------------------------------ #
# Operaciones genéricas de bytes                                       #
# ------------------------------------------------------------------ #

def subir_bytes(bucket: str, nombre: str, datos: bytes, content_type: str = "application/octet-stream") -> str:
    get_client().put_object(bucket, nombre, BytesIO(datos), len(datos), content_type=content_type)
    url = f"minio://{bucket}/{nombre}"
    logger.debug("Bytes subidos: %s", url)
    return url


def descargar_bytes(bucket: str, nombre: str) -> bytes:
    return get_client().get_object(bucket, nombre).read()
