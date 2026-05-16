"""
DAG Fase 2 — Predicción sobre nuevos audios
Procesa audios en data/audios/ con Whisper → Mistral → modelo ML.
Lanza dag_evaluation automáticamente al terminar.
"""

import io
import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path

import joblib
import numpy as np

from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.operators.trigger_dagrun import TriggerDagRunOperator

from pipeline.config import DATABASE_URL, DATA_DIR, MISTRAL_API_KEY
from pipeline.db import DatabaseService
from pipeline.llm import LLMService
from pipeline.minio_client import (
    BUCKET_AUDIOS,
    BUCKET_MODELOS,
    descargar_bytes,
    subir_bytes,
    subir_json,
    subir_texto,
)
from pipeline.prompts import SYSTEM_PROMPT

logger = logging.getLogger(__name__)

_EXT_AUDIO          = {".mp3", ".wav", ".m4a", ".ogg", ".webm", ".flac"}
_NIVELES_VALIDOS    = {"C1", "C2", "C3", "C4", "C5"}
_REINTENTOS_LLM     = 4
_ESPERA_429_INICIAL = 15


# ── Helpers ──────────────────────────────────────────────────────────────────

def _generar_guid(nombre_audio: str) -> str:
    """GUID determinista basado en el nombre del archivo — permite reanudación."""
    stem = Path(nombre_audio).stem
    safe = re.sub(r"[^A-Za-z0-9]+", "_", stem).strip("_")[:30].upper()
    return f"FASE2-{safe}" if safe else f"FASE2-{int(time.time())}"


def _extraer_json(texto: str) -> dict:
    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        pass
    sin_md = re.sub(r"```(?:json)?\s*", "", texto).strip()
    try:
        return json.loads(sin_md)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", sin_md, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    raise ValueError(f"JSON inválido en respuesta LLM: {texto[:200]}")


def _parsear_score(valor) -> float:
    if isinstance(valor, (int, float)):
        return float(valor)
    texto = str(valor).strip().split("/")[0].strip()
    try:
        return float(texto)
    except ValueError:
        return 0.0


def _llamar_mistral_con_retry(llm: LLMService, texto: str) -> tuple[dict, float]:
    espera = _ESPERA_429_INICIAL
    for intento in range(_REINTENTOS_LLM):
        try:
            raw, dur = llm.procesar_caso(SYSTEM_PROMPT, texto)
            return _extraer_json(raw), dur
        except Exception as exc:
            if "429" in str(exc) and intento < _REINTENTOS_LLM - 1:
                logger.warning("Rate limit 429 — espera %ds (intento %d)", espera, intento + 1)
                time.sleep(espera)
                espera = min(espera * 2, 120)
            else:
                raise


def _predecir(artefacto: dict, entidades_norm: list, score: float) -> str:
    vec   = artefacto["vectorizador"]
    clf   = artefacto["modelo"]
    texto = " ".join(entidades_norm)
    X_vec = vec.transform([texto]).toarray()
    X     = np.hstack([X_vec, [[score]]])
    return str(clf.predict(X)[0])


# ── Tarea principal ──────────────────────────────────────────────────────────

def _procesar_audios(**context):
    db  = DatabaseService(DATABASE_URL)
    llm = LLMService(MISTRAL_API_KEY)

    audios_dir = Path(DATA_DIR) / "audios"
    audios_dir.mkdir(parents=True, exist_ok=True)

    audios = sorted(
        p for p in audios_dir.iterdir()
        if p.is_file() and p.suffix.lower() in _EXT_AUDIO
    )

    if not audios:
        logger.info("No hay audios en %s — nada que procesar", audios_dir)
        return

    logger.info("→ %d audios encontrados en %s", len(audios), audios_dir)

    # Cargar Whisper y modelo ML una sola vez
    logger.info("Cargando Whisper base (cacheado)...")
    import whisper  # import diferido — pesado
    modelo_whisper = whisper.load_model("base")

    logger.info("Descargando modelo ML desde MinIO...")
    modelo_bytes     = descargar_bytes(BUCKET_MODELOS, "modelo_triageia.pkl")
    artefacto_modelo = joblib.load(io.BytesIO(modelo_bytes))

    procesados = 0
    errores    = 0
    saltados   = 0

    for audio_path in audios:
        guid = _generar_guid(audio_path.name)

        # Reanudación: si la entrevista ya existe y está completa, saltar
        if db.existe_entrevista(guid):
            logger.info("⏭  %s ya procesado (audio %s)", guid, audio_path.name)
            saltados += 1
            continue

        try:
            now = datetime.now()
            ext = audio_path.suffix.lstrip(".").lower()

            # 1. Subir audio a MinIO + crear entrevista
            audio_bytes = audio_path.read_bytes()
            url_audio   = subir_bytes(
                BUCKET_AUDIOS, f"{guid}.{ext}", audio_bytes,
                content_type=f"audio/{ext}",
            )
            db.crear_entrevista(guid, url_audio)
            db.actualizar_entrevista(
                guid,
                motor_workflow="Airflow_Fase2",
                estado="PROCESANDO",
                inicio_preprocesamiento=now,
            )

            # 2. Whisper
            t_w0 = time.time()
            transcripcion = modelo_whisper.transcribe(str(audio_path))["text"].strip()
            dur_whisper = time.time() - t_w0
            fin_whisper = datetime.now()

            if not transcripcion:
                raise ValueError("Whisper devolvió transcripción vacía")

            subir_texto(guid, transcripcion)
            db.actualizar_entrevista(
                guid,
                fin_preprocesamiento=fin_whisper,
                inicio_extraccion_entidades=fin_whisper,
            )

            # 3. Mistral
            datos, dur_llm = _llamar_mistral_con_retry(llm, transcripcion)
            fin_llm = datetime.now()

            etiqueta = datos.get("triage_real", "")
            if etiqueta not in _NIVELES_VALIDOS:
                raise ValueError(f"triage_real inválido: '{etiqueta}'")

            db.actualizar_entrevista(
                guid,
                fin_extraccion_entidades=fin_llm,
                inicio_normalizacion=fin_llm, fin_normalizacion=fin_llm,
                inicio_etiquetado=fin_llm, fin_etiquetado=fin_llm,
                inicio_score=fin_llm, fin_score=fin_llm,
            )

            # 4. Predicción ML
            entidades_norm = datos.get("entidades_normalizadas", [])
            score_ans      = _parsear_score(datos.get("score_ansiedad", 0.0))

            t_pred_inicio = datetime.now()
            pred          = _predecir(artefacto_modelo, entidades_norm, score_ans)
            fin_pred      = datetime.now()

            # 5. Guardar JSON enriquecido (incluye prediccion_prueba como exige proyecto.txt)
            enriquecido = {
                "guid":                   guid,
                "origen":                 "Fase2",
                "categoria":              "FASE2",
                "texto":                  transcripcion,
                "resumen":                datos.get("resumen_es", ""),
                "entidades":              datos.get("entidades_extraidas", []),
                "entidades_normalizadas": entidades_norm,
                "etiqueta":               etiqueta,
                "razonamiento":           datos.get("justificacion", ""),
                "score_ansiedad":         score_ans,
                "prediccion_entrenada":   pred,   # rellena la columna estándar
                "prediccion_prueba":      pred,   # nomenclatura del enunciado para Fase 2
                "archivo_audio":          audio_path.name,
                "duracion_whisper_seg":   round(dur_whisper, 2),
                "duracion_llm_seg":       round(dur_llm, 2),
            }
            subir_json(guid, enriquecido)

            db.actualizar_entrevista(
                guid,
                inicio_entrenamiento=t_pred_inicio,
                fin_entrenamiento=fin_pred,
                fin_solicitud=fin_pred,
                estado="PREDICCION_COMPLETADA",
                url_modelo_entrenado=f"minio://{BUCKET_MODELOS}/modelo_triageia.pkl",
            )

            procesados += 1
            logger.info(
                "✓ %s  whisper=%.1fs  llm=%.1fs  etiqueta=%s  prediccion=%s",
                guid, dur_whisper, dur_llm, etiqueta, pred,
            )

        except Exception as exc:
            errores += 1
            try:
                db.actualizar_entrevista(guid, estado="ERROR")
            except Exception:
                pass
            logger.error("✗ %s (%s): %s", guid, audio_path.name, exc)

    logger.info(
        "✓ Fase 2 completada — procesados=%d  saltados=%d  errores=%d",
        procesados, saltados, errores,
    )


# ── DAG ──────────────────────────────────────────────────────────────────────

with DAG(
    dag_id="dag_prediction_phase_2",
    description="Fase 2 — Audio → Whisper → Mistral → modelo ML → prediccion_prueba",
    start_date=datetime(2026, 1, 1),
    schedule=None,
    catchup=False,
    default_args={"retries": 1, "retry_delay": 60},
    tags=["fase2"],
) as dag:

    procesar = PythonOperator(
        task_id="procesar_audios",
        python_callable=_procesar_audios,
    )

    trigger_eval = TriggerDagRunOperator(
        task_id="trigger_evaluation",
        trigger_dag_id="dag_evaluation",
        wait_for_completion=False,
    )

    procesar >> trigger_eval
