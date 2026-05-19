"""
DAG Fase 1 — Paso 1: Ingesta
Lee los archivos .info, parsea las conversaciones, sube cada texto a MinIO
y genera conversaciones.csv. Al terminar lanza automáticamente
dag_llm_enrichment.

Nota: este DAG NO guarda nada en Postgres. La base de datos solo se usa
para las predicciones en tiempo real (Fase 3, desde la API/Frontend). El
artefacto compartido entre DAGs es MinIO (idempotencia: put_object
sobrescribe el objeto si ya existe).
"""

import io
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.operators.trigger_dagrun import TriggerDagRunOperator

from pipeline.config import DATA_DIR
from pipeline.minio_client import subir_texto, subir_bytes, BUCKET_DATASETS
from pipeline.parser import cargar_dataset

logger = logging.getLogger(__name__)


def _ingestar(**context):
    raw_dir = Path(DATA_DIR) / "raw"

    logger.info("Leyendo archivos .info desde: %s", raw_dir)
    casos = cargar_dataset(raw_dir)
    logger.info("%d casos encontrados", len(casos))

    registros = []
    t_inicio  = datetime.now()

    # Orden por categoría + id para que el CSV quede agrupado y ordenado
    items_ord = sorted(casos.items(), key=lambda kv: (kv[1].categoria, kv[0]))

    for guid, caso in items_ord:
        origen = "Simulación" if guid.startswith("SIM") else caso.origen  # train / test
        subir_texto(guid, caso.transcripcion)
        logger.info("✓ %s subido a MinIO (%s)", guid, origen)

        registros.append({
            "guid":       guid,
            "origen":     origen,
            "categoria":  caso.categoria,
            "num_turnos": caso.num_turnos,
            "texto":      caso.transcripcion,
        })

    df = pd.DataFrame(registros)
    csv_bytes = df.to_csv(index=False).encode("utf-8")

    # 1) MinIO
    subir_bytes(BUCKET_DATASETS, "conversaciones.csv", csv_bytes, content_type="text/csv")

    # 2) Copia local accesible desde el host (./data/processed/)
    local_dir = os.path.join(DATA_DIR, "processed")
    os.makedirs(local_dir, exist_ok=True)
    local_path = os.path.join(local_dir, "conversaciones.csv")
    with open(local_path, "wb") as f:
        f.write(csv_bytes)

    duracion = (datetime.now() - t_inicio).total_seconds()
    logger.info("✓ Ingesta completada: %d casos en %.1fs", len(casos), duracion)
    logger.info("✓ conversaciones.csv → MinIO + %s", local_path)


with DAG(
    dag_id="dag_ingestion",
    description="Fase 1 — Paso 1: .info → conversaciones.csv en MinIO",
    start_date=datetime(2026, 1, 1),
    schedule=None,
    catchup=False,
    default_args={"retries": 1, "retry_delay": timedelta(seconds=30)},
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
