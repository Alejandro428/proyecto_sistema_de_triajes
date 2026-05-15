import json
import logging
import os
from io import BytesIO

from minio import Minio

logger = logging.getLogger(__name__)

BUCKET_AUDIOS       = "audios"
BUCKET_TEXTOS       = "textos"
BUCKET_ENRIQUECIDOS = "enriquecidos"
BUCKET_DATASETS     = "datasets"
BUCKET_MODELOS      = "modelos"

_BUCKETS = [BUCKET_AUDIOS, BUCKET_TEXTOS, BUCKET_ENRIQUECIDOS, BUCKET_DATASETS, BUCKET_MODELOS]

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
# Operaciones de texto                                                 #
# ------------------------------------------------------------------ #

def subir_texto(guid: str, contenido: str) -> str:
    client = get_client()
    nombre = f"{guid}.txt"
    data   = contenido.encode("utf-8")
    client.put_object(BUCKET_TEXTOS, nombre, BytesIO(data), len(data), content_type="text/plain")
    url = f"minio://{BUCKET_TEXTOS}/{nombre}"
    logger.debug("Texto subido: %s", url)
    return url


def descargar_texto(url: str) -> str:
    partes  = url.replace("minio://", "").split("/", 1)
    bucket, nombre = partes[0], partes[1]
    return get_client().get_object(bucket, nombre).read().decode("utf-8")


# ------------------------------------------------------------------ #
# Operaciones de JSON enriquecido                                      #
# ------------------------------------------------------------------ #

def subir_json(guid: str, datos: dict) -> str:
    client = get_client()
    nombre = f"{guid}.json"
    data   = json.dumps(datos, ensure_ascii=False).encode("utf-8")
    client.put_object(BUCKET_ENRIQUECIDOS, nombre, BytesIO(data), len(data), content_type="application/json")
    url = f"minio://{BUCKET_ENRIQUECIDOS}/{nombre}"
    logger.debug("JSON subido: %s", url)
    return url


def descargar_json(guid: str) -> dict:
    nombre = f"{guid}.json"
    data   = get_client().get_object(BUCKET_ENRIQUECIDOS, nombre).read()
    return json.loads(data)


def json_existe(guid: str) -> bool:
    try:
        get_client().stat_object(BUCKET_ENRIQUECIDOS, f"{guid}.json")
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
