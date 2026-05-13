import os
from io import BytesIO

from minio import Minio

BUCKET_TEXTOS   = "textos"
BUCKET_DATASETS = "datasets"
BUCKET_MODELOS  = "modelos"


def get_client() -> Minio:
    return Minio(
        os.environ.get("MINIO_ENDPOINT", "minio:9000"),
        access_key=os.environ.get("MINIO_ACCESS_KEY", "minioadmin"),
        secret_key=os.environ.get("MINIO_SECRET_KEY", "minioadmin"),
        secure=False,
    )


def inicializar_buckets() -> None:
    client = get_client()
    for bucket in [BUCKET_TEXTOS, BUCKET_DATASETS, BUCKET_MODELOS]:
        if not client.bucket_exists(bucket):
            client.make_bucket(bucket)


def subir_texto(guid: str, contenido: str) -> str:
    client = get_client()
    inicializar_buckets()
    nombre = f"{guid}.txt"
    data = contenido.encode("utf-8")
    client.put_object(BUCKET_TEXTOS, nombre, BytesIO(data), len(data), content_type="text/plain")
    return f"minio://{BUCKET_TEXTOS}/{nombre}"


def subir_bytes(bucket: str, nombre: str, datos: bytes) -> str:
    client = get_client()
    inicializar_buckets()
    client.put_object(bucket, nombre, BytesIO(datos), len(datos))
    return f"minio://{bucket}/{nombre}"


def descargar_bytes(bucket: str, nombre: str) -> bytes:
    client = get_client()
    return client.get_object(bucket, nombre).read()
