"""
DAG Fase 1 — Paso 2: Enriquecimiento LLM
Lee conversaciones.csv de MinIO, llama a Mistral por cada caso,
guarda un JSON por caso (reanudable si falla), y genera dataset_entrenamiento.csv.
Se lanza automáticamente cuando termina dag_ingestion.
"""

import io
import json
import logging
import re
import time
from datetime import datetime, timedelta

import pandas as pd
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.operators.trigger_dagrun import TriggerDagRunOperator

from pipeline.config import DATABASE_URL, MISTRAL_API_KEY
from pipeline.db import DatabaseService
from pipeline.llm import LLMService
from pipeline.minio_client import (
    descargar_bytes,
    descargar_json,
    json_existe,
    subir_bytes,
    subir_json,
    BUCKET_DATASETS,
)
from pipeline.prompts import SYSTEM_PROMPT

logger = logging.getLogger(__name__)

_SLEEP_ENTRE_LLAMADAS = 1.5
_REINTENTOS_MAX       = 6
_ESPERA_INICIAL_429   = 15


# ------------------------------------------------------------------ #
# Helpers de parseo y validación                                       #
# ------------------------------------------------------------------ #

def _extraer_json(texto: str) -> dict:
    """Intenta parsear JSON directo; si falla limpia markdown y reintenta."""
    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        pass
    # Mistral a veces envuelve en bloques ```json … ```
    sin_md = re.sub(r"```(?:json)?\s*", "", texto).strip()
    try:
        return json.loads(sin_md)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", sin_md, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    raise ValueError(f"JSON inválido en respuesta LLM: {texto[:200]}")


def _validar_datos(datos: dict, guid: str) -> None:
    """Lanza ValueError si faltan campos obligatorios o el nivel es inválido."""
    for campo in ("triage_real", "entidades_normalizadas", "score_ansiedad"):
        if not datos.get(campo) and datos.get(campo) != 0:
            raise ValueError(f"{guid}: campo requerido vacío: '{campo}'")
    if datos["triage_real"] not in {"C1", "C2", "C3", "C4", "C5"}:
        raise ValueError(f"{guid}: triage_real inválido: '{datos['triage_real']}'")


def _parsear_score(valor) -> float:
    """Convierte cualquier representación del score a float."""
    if isinstance(valor, (int, float)):
        return float(valor)
    texto = str(valor).strip().split("/")[0].strip()
    try:
        return float(texto)
    except ValueError:
        return 0.0


# ------------------------------------------------------------------ #
# Llamada LLM con retry y back-off                                     #
# ------------------------------------------------------------------ #

def _llamar_con_retry(llm: LLMService, texto: str) -> tuple[dict, float, int]:
    """
    Retorna (datos_json, duracion_llm_segundos, num_reintentos).
    """
    espera       = _ESPERA_INICIAL_429
    reintentos   = 0
    duracion_llm = 0.0

    for intento in range(_REINTENTOS_MAX):
        try:
            raw, duracion_llm = llm.procesar_caso(SYSTEM_PROMPT, texto)
            time.sleep(_SLEEP_ENTRE_LLAMADAS)
            datos = _extraer_json(raw)
            return datos, duracion_llm, reintentos
        except Exception as exc:
            if "429" in str(exc) and intento < _REINTENTOS_MAX - 1:
                reintentos += 1
                logger.warning("Rate limit 429 — esperando %ds (intento %d)", espera, intento + 1)
                time.sleep(espera)
                espera = min(espera * 2, 120)
            else:
                raise


# ------------------------------------------------------------------ #
# Tarea principal                                                      #
# ------------------------------------------------------------------ #

def _enriquecer(**context):
    db  = DatabaseService(DATABASE_URL)
    llm = LLMService(MISTRAL_API_KEY)

    csv_bytes = descargar_bytes(BUCKET_DATASETS, "conversaciones.csv")
    df_conv   = pd.read_csv(io.BytesIO(csv_bytes))
    logger.info("→ %d casos en conversaciones.csv", len(df_conv))

    procesados   = 0
    errores      = 0
    t_dag_inicio = datetime.now()

    for _, fila in df_conv.iterrows():
        guid   = fila["guid"]
        texto  = fila["texto"]
        origen = fila["origen"]

        if json_existe(guid):
            logger.info("⏭  %s ya enriquecido", guid)
            procesados += 1
            continue

        try:
            now = datetime.now()
            db.actualizar_entrevista(guid, estado="PROCESANDO", inicio_preprocesamiento=now)

            # Preprocesamiento (limpieza básica del texto)
            texto_limpio = texto.strip()
            fin_prep     = datetime.now()
            db.actualizar_entrevista(guid, fin_preprocesamiento=fin_prep,
                                     inicio_extraccion_entidades=fin_prep)

            # Llamada LLM (extrae entidades, normaliza, etiqueta y calcula score en una sola llamada)
            datos, duracion_llm, reintentos = _llamar_con_retry(llm, texto_limpio)
            fin_llm = datetime.now()

            _validar_datos(datos, guid)

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
                "score_ansiedad":         _parsear_score(datos.get("score_ansiedad", 0.0)),
                "prediccion_entrenada":   "",
            }
            subir_json(guid, enriquecido)

            db.actualizar_entrevista(
                guid,
                estado="SCORE_CALCULADO",
                fin_extraccion_entidades=fin_llm,
                inicio_normalizacion=fin_llm,
                fin_normalizacion=fin_llm,
                inicio_etiquetado=fin_llm,
                fin_etiquetado=fin_llm,
                inicio_score=fin_llm,
                fin_score=fin_llm,
            )

            procesados += 1
            logger.info(
                "✓ %s (%s) → %s  ansiedad=%.2f  llm=%.1fs  reintentos=%d",
                guid, origen,
                datos.get("triage_real"),
                _parsear_score(datos.get("score_ansiedad", 0)),
                duracion_llm,
                reintentos,
            )

        except Exception as exc:
            errores += 1
            db.actualizar_entrevista(guid, estado="ERROR")
            logger.error("✗ %s: %s", guid, exc)

    # ---- Construir dataset_entrenamiento.csv ----
    logger.info("→ Construyendo dataset_entrenamiento.csv...")
    registros = []
    guids_ok  = []

    for _, fila in df_conv.iterrows():
        try:
            registros.append(descargar_json(fila["guid"]))
            guids_ok.append(fila["guid"])
        except Exception as exc:
            logger.warning("Sin JSON para %s: %s", fila["guid"], exc)

    df_train = pd.DataFrame(registros)
    for col in ("entidades", "entidades_normalizadas"):
        if col in df_train.columns:
            df_train[col] = df_train[col].apply(
                lambda x: json.dumps(x, ensure_ascii=False) if isinstance(x, list) else x
            )

    csv_bytes = df_train.to_csv(index=False).encode("utf-8")
    subir_bytes(BUCKET_DATASETS, "dataset_entrenamiento.csv", csv_bytes, content_type="text/csv")
    logger.info("✓ dataset_entrenamiento.csv guardado (%d casos)", len(df_train))

    for guid in guids_ok:
        db.actualizar_entrevista(guid, estado="DATASET_GENERADO")

    duracion_total = (datetime.now() - t_dag_inicio).total_seconds()
    throughput     = procesados / (duracion_total / 60) if duracion_total > 0 else 0

    logger.info(
        "✓ Enriquecimiento completado: %d ok / %d errores / %.0f casos·min⁻¹ / total %.0fs",
        procesados, errores, throughput, duracion_total,
    )


with DAG(
    dag_id="dag_llm_enrichment",
    description="Fase 1 — Paso 2: conversaciones.csv → Mistral → dataset_entrenamiento.csv",
    start_date=datetime(2026, 1, 1),
    schedule=None,
    catchup=False,
    default_args={"retries": 1, "retry_delay": timedelta(seconds=60)},
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
