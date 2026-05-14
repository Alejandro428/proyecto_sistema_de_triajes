import json
import os
from io import BytesIO

from minio import Minio

BUCKET_AUDIOS       = "audios"
BUCKET_TEXTOS       = "textos"
BUCKET_ENRIQUECIDOS = "enriquecidos"
BUCKET_DATASETS     = "datasets"
BUCKET_MODELOS      = "modelos"

_BUCKETS = [BUCKET_AUDIOS, BUCKET_TEXTOS, BUCKET_ENRIQUECIDOS, BUCKET_DATASETS, BUCKET_MODELOS]


def get_client() -> Minio:
    return Minio(
        os.environ.get("MINIO_ENDPOINT", "minio:9000"),
        access_key=os.environ.get("MINIO_ACCESS_KEY", "minioadmin"),
        secret_key=os.environ.get("MINIO_SECRET_KEY", "minioadmin"),
        secure=False,
    )


def inicializar_buckets() -> None:
    client = get_client()
    for bucket in _BUCKETS:
        if not client.bucket_exists(bucket):
            client.make_bucket(bucket)


def subir_texto(guid: str, contenido: str) -> str:
    client = get_client()
    inicializar_buckets()
    nombre = f"{guid}.txt"
    data = contenido.encode("utf-8")
    client.put_object(BUCKET_TEXTOS, nombre, BytesIO(data), len(data), content_type="text/plain")
    return f"minio://{BUCKET_TEXTOS}/{nombre}"


def descargar_texto(url: str) -> str:
    # url formato: minio://textos/{guid}.txt
    partes = url.replace("minio://", "").split("/", 1)
    bucket, nombre = partes[0], partes[1]
    client = get_client()
    return client.get_object(bucket, nombre).read().decode("utf-8")


def subir_json(guid: str, datos: dict) -> str:
    client = get_client()
    inicializar_buckets()
    nombre = f"{guid}.json"
    data = json.dumps(datos, ensure_ascii=False).encode("utf-8")
    client.put_object(BUCKET_ENRIQUECIDOS, nombre, BytesIO(data), len(data), content_type="application/json")
    return f"minio://{BUCKET_ENRIQUECIDOS}/{nombre}"


def descargar_json(guid: str) -> dict:
    client = get_client()
    nombre = f"{guid}.json"
    data = client.get_object(BUCKET_ENRIQUECIDOS, nombre).read()
    return json.loads(data)


def subir_bytes(bucket: str, nombre: str, datos: bytes) -> str:
    client = get_client()
    inicializar_buckets()
    client.put_object(bucket, nombre, BytesIO(datos), len(datos))
    return f"minio://{bucket}/{nombre}"


def descargar_bytes(bucket: str, nombre: str) -> bytes:
    client = get_client()
    return client.get_object(bucket, nombre).read()
