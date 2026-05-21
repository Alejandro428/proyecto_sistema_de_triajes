"""
Cliente MinIO + helpers para el frontend Streamlit.
"""

import json
import os

import streamlit as st
from minio import Minio

MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT",  "localhost:9000")
MINIO_ACCESS   = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET   = os.getenv("MINIO_SECRET_KEY", "minioadmin")

BUCKET_ENRIQUECIDOS = "enriquecidos"


@st.cache_resource
def get_minio() -> Minio:
    client = Minio(MINIO_ENDPOINT, access_key=MINIO_ACCESS, secret_key=MINIO_SECRET, secure=False)
    for b in [BUCKET_ENRIQUECIDOS, "audios", "textos", "datasets"]:
        if not client.bucket_exists(b):
            client.make_bucket(b)
    return client


def descargar_json(guid: str) -> dict | None:
    try:
        data = get_minio().get_object(BUCKET_ENRIQUECIDOS, f"{guid}.json").read()
        return json.loads(data)
    except Exception:
        return None


