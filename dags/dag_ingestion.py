"""
DAG Fase 1 - Paso 1: Ingesta
Lee los archivos .info, parsea las conversaciones, guarda en Postgres y MinIO.
Trigger: manual desde la UI de Airflow.
"""

import os
from datetime import datetime
from pathlib import Path

from airflow import DAG
from airflow.operators.python import PythonOperator

from pipeline.parser import cargar_dataset
from pipeline.db import DatabaseService
from pipeline.minio_client import subir_texto

DATABASE_URL = os.environ["DATABASE_URL"]
DATA_DIR = Path(os.environ.get("DATA_DIR", "/opt/airflow/data"))


def _ingestar(**context):
    db = DatabaseService(DATABASE_URL)
    raw_dir = DATA_DIR / "raw"

    print(f"→ Leyendo archivos .info desde: {raw_dir}")
    casos = cargar_dataset(raw_dir)
    print(f"  {len(casos)} casos encontrados")

    nuevos = 0
    for guid, caso in casos.items():
        if db.existe_entrevista(guid):
            print(f"⏭  {guid} ya existe, saltando")
            continue

        origen = "Simulacion" if guid.startswith("SIM") else "Dataset"
        url = subir_texto(guid, caso.transcripcion)
        db.crear_entrevista(guid, origen, url)
        db.insertar_transcripcion(guid, caso.transcripcion)
        nuevos += 1
        print(f"✓ {guid} ingestado ({origen})")

    print(f"\n✓ Ingesta completada: {nuevos} casos nuevos de {len(casos)} totales")


with DAG(
    dag_id="dag_ingestion",
    description="Fase 1 - Paso 1: Parsea .info → Postgres + MinIO",
    start_date=datetime(2026, 1, 1),
    schedule=None,
    catchup=False,
    tags=["fase1"],
) as dag:
    PythonOperator(
        task_id="ingestar_textos",
        python_callable=_ingestar,
    )
