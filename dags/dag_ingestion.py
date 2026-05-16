"""
DAG Fase 1 — Paso 1: Ingesta
Lee los archivos .info, parsea las conversaciones, sube cada texto a MinIO,
genera conversaciones.csv y crea filas de tracking en Postgres.
Al terminar lanza automáticamente dag_llm_enrichment.
"""

import io
import logging
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.operators.trigger_dagrun import TriggerDagRunOperator

from pipeline.config import DATABASE_URL, DATA_DIR
from pipeline.db import DatabaseService
from pipeline.minio_client import subir_texto, subir_bytes, BUCKET_DATASETS
from pipeline.parser import cargar_dataset

logger = logging.getLogger(__name__)


def _ingestar(**context):
    db      = DatabaseService(DATABASE_URL)
    raw_dir = Path(DATA_DIR) / "raw"

    logger.info("Leyendo archivos .info desde: %s", raw_dir)
    casos = cargar_dataset(raw_dir)
    logger.info("%d casos encontrados", len(casos))

    registros = []
    nuevos    = 0
    t_inicio  = datetime.now()

    for guid, caso in casos.items():
        origen = "Simulación" if guid.startswith("SIM") else "Dataset"

        if db.existe_entrevista(guid):
            logger.info("⏭  %s ya existe, saltando", guid)
        else:
            url = subir_texto(guid, caso.transcripcion)
            db.crear_entrevista(guid, url)
            nuevos += 1
            logger.info("✓ %s ingestado (%s)", guid, origen)

        registros.append({
            "guid":      guid,
            "origen":    origen,
            "categoria": caso.categoria,
            "texto":     caso.transcripcion,
        })

    df        = pd.DataFrame(registros)
    csv_bytes = df.to_csv(index=False).encode("utf-8")
    subir_bytes(BUCKET_DATASETS, "conversaciones.csv", csv_bytes, content_type="text/csv")

    duracion = (datetime.now() - t_inicio).total_seconds()
    logger.info(
        "✓ Ingesta completada: %d nuevos / %d totales en %.1fs",
        nuevos, len(casos), duracion,
    )


with DAG(
    dag_id="dag_ingestion",
    description="Fase 1 — Paso 1: .info → conversaciones.csv en MinIO + tracking en Postgres",
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
