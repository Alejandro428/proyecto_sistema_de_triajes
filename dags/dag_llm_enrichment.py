"""
DAG Fase 1 - Paso 2: Enriquecimiento LLM
Lee conversaciones.csv de MinIO, llama a Mistral por cada caso,
guarda un JSON por caso para poder reanudar si falla,
y al final genera dataset_entrenamiento.csv con las columnas del enunciado.
Se lanza automáticamente cuando termina dag_ingestion.
"""

import io
import json
import os
import re
import time
from datetime import datetime

import pandas as pd
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.operators.trigger_dagrun import TriggerDagRunOperator

from pipeline.llm import LLMService
from pipeline.db import DatabaseService
from pipeline.prompts import SYSTEM_PROMPT
from pipeline.minio_client import (
    descargar_bytes,
    subir_json,
    descargar_json,
    subir_bytes,
    BUCKET_DATASETS,
)

DATABASE_URL    = os.environ["DATABASE_URL"]
MISTRAL_API_KEY = os.environ["MISTRAL_API_KEY"]

_SLEEP_ENTRE_LLAMADAS = 1.5
_REINTENTOS_MAX       = 6
_ESPERA_INICIAL_429   = 15


def _extraer_json(texto: str) -> dict:
    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", texto, re.DOTALL)
    if match:
        return json.loads(match.group())
    raise ValueError(f"JSON inválido en respuesta LLM: {texto[:200]}")


def _llamar_con_retry(llm, texto: str) -> dict:
    espera = _ESPERA_INICIAL_429
    for intento in range(_REINTENTOS_MAX):
        try:
            raw = llm.procesar_caso(SYSTEM_PROMPT, texto)
            time.sleep(_SLEEP_ENTRE_LLAMADAS)
            return _extraer_json(raw)
        except Exception as e:
            if "429" in str(e) and intento < _REINTENTOS_MAX - 1:
                print(f"  ⏳ Rate limit, esperando {espera}s (intento {intento+1})...")
                time.sleep(espera)
                espera = min(espera * 2, 120)
            else:
                raise


def _ya_procesado(guid: str) -> bool:
    try:
        descargar_json(guid)
        return True
    except Exception:
        return False


def _enriquecer(**context):
    db  = DatabaseService(DATABASE_URL)
    llm = LLMService(MISTRAL_API_KEY)

    csv_bytes = descargar_bytes(BUCKET_DATASETS, "conversaciones.csv")
    df_conv = pd.read_csv(io.BytesIO(csv_bytes))
    print(f"→ {len(df_conv)} casos en conversaciones.csv")

    procesados = 0
    errores    = 0

    for _, fila in df_conv.iterrows():
        guid   = fila["guid"]
        texto  = fila["texto"]
        origen = fila["origen"]

        if _ya_procesado(guid):
            print(f"⏭  {guid} ya enriquecido, saltando")
            procesados += 1
            continue

        try:
            now = datetime.now()
            db.actualizar_entrevista(guid, estado="PROCESANDO", inicio_preprocesamiento=now)
            db.actualizar_entrevista(guid, fin_preprocesamiento=datetime.now())
            db.actualizar_entrevista(guid, inicio_extraccion_entidades=datetime.now())

            datos = _llamar_con_retry(llm, texto)
            fin   = datetime.now()

            # Columnas según el enunciado: id, origen, texto, entidades,
            # entidades_normalizadas, etiqueta, score_ansiedad, prediccion_entrenada
            enriquecido = {
                "guid":                   guid,
                "origen":                 origen,
                "categoria":              fila["categoria"],
                "texto":                  texto,
                "resumen":                datos.get("resumen_es", ""),
                "entidades":              datos.get("entidades_extraidas", []),
                "entidades_normalizadas": datos.get("entidades_normalizadas", []),
                "etiqueta":               datos.get("triage_real", ""),
                "razonamiento":           datos.get("justificacion", ""),
                "score_ansiedad":         float(datos.get("score_ansiedad", 0.0)),
                "prediccion_entrenada":   "",
            }
            subir_json(guid, enriquecido)

            db.actualizar_entrevista(
                guid,
                estado="SCORE_CALCULADO",
                fin_extraccion_entidades=fin,
                inicio_normalizacion=fin,
                fin_normalizacion=fin,
                inicio_etiquetado=fin,
                fin_etiquetado=fin,
                inicio_score=fin,
                fin_score=fin,
            )

            procesados += 1
            print(f"✓ {guid} ({origen}) → {datos.get('triage_real')}  ansiedad={datos.get('score_ansiedad')}")

        except Exception as e:
            errores += 1
            db.actualizar_entrevista(guid, estado="ERROR")
            print(f"✗ {guid}: {e}")

    # Construir dataset_entrenamiento.csv desde los JSONs por caso
    print("\n→ Construyendo dataset_entrenamiento.csv...")
    registros = []
    guids_ok  = []
    for _, fila in df_conv.iterrows():
        try:
            registros.append(descargar_json(fila["guid"]))
            guids_ok.append(fila["guid"])
        except Exception as e:
            print(f"⚠ Sin JSON para {fila['guid']}: {e}")

    df_train = pd.DataFrame(registros)
    for col in ["entidades", "entidades_normalizadas"]:
        if col in df_train.columns:
            df_train[col] = df_train[col].apply(
                lambda x: json.dumps(x, ensure_ascii=False) if isinstance(x, list) else x
            )

    csv_bytes = df_train.to_csv(index=False).encode("utf-8")
    subir_bytes(BUCKET_DATASETS, "dataset_entrenamiento.csv", csv_bytes)
    print(f"✓ dataset_entrenamiento.csv guardado ({len(df_train)} casos)")

    # Marcar como DATASET_GENERADO (estado previo a entrenamiento)
    for guid in guids_ok:
        db.actualizar_entrevista(guid, estado="DATASET_GENERADO")
    print(f"✓ {len(guids_ok)} casos marcados como DATASET_GENERADO")

    print(f"\n✓ Enriquecimiento: {procesados} procesados, {errores} errores")


with DAG(
    dag_id="dag_llm_enrichment",
    description="Fase 1 - Paso 2: conversaciones.csv → Mistral → dataset_entrenamiento.csv",
    start_date=datetime(2026, 1, 1),
    schedule=None,
    catchup=False,
    tags=["fase1"],
) as dag:
    enriquecer = PythonOperator(
        task_id="enriquecer_con_llm",
        python_callable=_enriquecer,
    )
    trigger = TriggerDagRunOperator(
        task_id="trigger_model_training",
        trigger_dag_id="dag_model_training",
        wait_for_completion=False,
    )
    enriquecer >> trigger
