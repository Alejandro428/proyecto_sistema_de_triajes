"""
DAG Fase 1 - Paso 2: Enriquecimiento LLM
Lee casos pendientes de Postgres, llama a Mistral por cada uno,
guarda entidades, nivel de urgencia, razonamiento y score de ansiedad.
Trigger: manual, después de dag_ingestion.
"""

import json
import os
import re
import time
from datetime import datetime

from airflow import DAG
from airflow.operators.python import PythonOperator

from pipeline.llm import LLMService
from pipeline.db import DatabaseService
from pipeline.prompts import SYSTEM_PROMPT

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


def _enriquecer(**context):
    db  = DatabaseService(DATABASE_URL)
    llm = LLMService(MISTRAL_API_KEY)

    pendientes = db.obtener_casos_pendientes()
    print(f"→ {len(pendientes)} casos pendientes de enriquecer")

    procesados = 0
    errores    = 0
    now        = datetime.now()

    for caso in pendientes:
        guid = caso["guid_entrevista"]
        try:
            db.actualizar_entrevista(
                guid,
                estado="PROCESANDO",
                inicio_preprocesamiento=now,
                fin_preprocesamiento=now,
                inicio_extraccion=now,
            )

            datos = _llamar_con_retry(llm, caso["transcripcion"])
            fin   = datetime.now()

            db.actualizar_caso_enriquecido({
                "guid_entrevista":    guid,
                "resumen":            datos.get("resumen_es", ""),
                "sintomas_detectados": datos.get("entidades_extraidas", []),
                "terminos_clinicos":  datos.get("entidades_normalizadas", []),
                "nivel_urgencia":     datos.get("triage_real", ""),
                "razonamiento":       datos.get("justificacion", ""),
                "nivel_ansiedad":     float(datos.get("score_ansiedad", 0.0)),
            })

            db.actualizar_entrevista(
                guid,
                estado="SCORE_CALCULADO",
                fin_extraccion=fin,
                inicio_normalizacion=fin,
                fin_normalizacion=fin,
                inicio_etiquetado=fin,
                fin_etiquetado=fin,
                inicio_score=fin,
                fin_score=fin,
            )

            procesados += 1
            print(f"✓ {guid} → {datos.get('triage_real')}  ansiedad={datos.get('score_ansiedad')}")

        except Exception as e:
            errores += 1
            db.actualizar_entrevista(guid, estado="ERROR")
            print(f"✗ {guid}: {e}")

    print(f"\n✓ Enriquecimiento completado: {procesados} procesados, {errores} errores")


with DAG(
    dag_id="dag_llm_enrichment",
    description="Fase 1 - Paso 2: Llama a Mistral por cada caso → Postgres",
    start_date=datetime(2026, 1, 1),
    schedule=None,
    catchup=False,
    tags=["fase1"],
) as dag:
    PythonOperator(
        task_id="enriquecer_con_llm",
        python_callable=_enriquecer,
    )
