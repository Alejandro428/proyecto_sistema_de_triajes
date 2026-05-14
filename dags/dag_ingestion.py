"""
DAG Fase 1 - Paso 1: Ingesta
Lee los archivos .info, parsea las conversaciones, sube cada texto a MinIO,
genera conversaciones.csv y crea filas de tracking en Postgres.
Al terminar lanza automáticamente dag_llm_enrichment.
"""

import io
import os
from datetime import datetime
from pathlib import Path

import pandas as pd
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.operators.trigger_dagrun import TriggerDagRunOperator

from pipeline.parser import cargar_dataset
from pipeline.db import DatabaseService
from pipeline.minio_client import subir_texto, subir_bytes, BUCKET_DATASETS

DATABASE_URL = os.environ["DATABASE_URL"]
DATA_DIR = Path(os.environ.get("DATA_DIR", "/opt/airflow/data"))


def _ingestar(**context):
    db = DatabaseService(DATABASE_URL)
    raw_dir = DATA_DIR / "raw"

    print(f"→ Leyendo archivos .info desde: {raw_dir}")
    casos = cargar_dataset(raw_dir)
    print(f"  {len(casos)} casos encontrados")

    registros = []
    nuevos = 0

    for guid, caso in casos.items():
        origen = "Simulación" if guid.startswith("SIM") else "Dataset"

        if db.existe_entrevista(guid):
            print(f"⏭  {guid} ya existe, saltando")
        else:
            url = subir_texto(guid, caso.transcripcion)
            db.crear_entrevista(guid, url)
            nuevos += 1
            print(f"✓ {guid} ingestado ({origen})")

        registros.append({
            "guid":      guid,
            "origen":    origen,
            "categoria": caso.categoria,
            "texto":     caso.transcripcion,
        })

    df = pd.DataFrame(registros)
    csv_bytes = df.to_csv(index=False).encode("utf-8")
    subir_bytes(BUCKET_DATASETS, "conversaciones.csv", csv_bytes)
    print(f"✓ conversaciones.csv guardado en MinIO ({len(df)} casos)")
    print(f"\n✓ Ingesta completada: {nuevos} casos nuevos de {len(casos)} totales")


with DAG(
    dag_id="dag_ingestion",
    description="Fase 1 - Paso 1: .info → conversaciones.csv en MinIO + tracking en Postgres",
    start_date=datetime(2026, 1, 1),
    schedule=None,
    catchup=False,
    tags=["fase1"],
) as dag:
    ingestar = PythonOperator(
        task_id="ingestar_textos",
        python_callable=_ingestar,
    )
    trigger = TriggerDagRunOperator(
        task_id="trigger_llm_enrichment",
        trigger_dag_id="dag_llm_enrichment",
        wait_for_completion=False,
    )
    ingestar >> trigger
