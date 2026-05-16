"""
DAG Fase 2 — Evaluación
Compara etiqueta (ground truth del LLM) vs prediccion_prueba (modelo ML)
para todos los casos de Fase 2. Calcula la valoración global y por caso.
"""

import io
import json
import logging
from datetime import datetime, timedelta

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import psycopg2
from sklearn.metrics import (
    ConfusionMatrixDisplay,
    accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
)

from airflow import DAG
from airflow.operators.python import PythonOperator

from pipeline.config import DATABASE_URL
from pipeline.db import DatabaseService
from pipeline.minio_client import (
    BUCKET_MODELOS,
    descargar_json,
    subir_bytes,
)

logger = logging.getLogger(__name__)

_NIVELES = ["C1", "C2", "C3", "C4", "C5"]


def _guids_fase2() -> list[str]:
    sql = """
        SELECT guid_entrevista
        FROM entrevista
        WHERE motor_workflow = 'Airflow_Fase2'
          AND estado IN ('PREDICCION_COMPLETADA', 'EVALUACION_COMPLETADA')
        ORDER BY guid_entrevista
    """
    with psycopg2.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            return [r[0] for r in cur.fetchall()]


def _evaluar(**context):
    db    = DatabaseService(DATABASE_URL)
    guids = _guids_fase2()

    if not guids:
        logger.warning("No hay casos de Fase 2 con predicción completada — nada que evaluar")
        return

    logger.info("→ Evaluando %d casos de Fase 2", len(guids))

    registros = []
    for guid in guids:
        try:
            datos = descargar_json(guid)
            registros.append({
                "guid":       guid,
                "etiqueta":   datos.get("etiqueta", ""),
                "prediccion": datos.get("prediccion_prueba")
                              or datos.get("prediccion_entrenada", ""),
            })
        except Exception as exc:
            logger.warning("Sin JSON para %s: %s", guid, exc)

    df = pd.DataFrame(registros)
    df = df[df["etiqueta"].isin(_NIVELES) & df["prediccion"].isin(_NIVELES)]

    if df.empty:
        logger.warning("Ningún caso con etiqueta + predicción válidas — abortando evaluación")
        return

    y_true = df["etiqueta"]
    y_pred = df["prediccion"]

    # Métricas globales
    acc  = accuracy_score(y_true, y_pred)
    prec = precision_score(y_true, y_pred, average="macro", zero_division=0)
    rec  = recall_score(y_true, y_pred, average="macro", zero_division=0)
    f1   = f1_score(y_true, y_pred, average="macro", zero_division=0)

    logger.info("--- Evaluación Fase 2 ---")
    logger.info(classification_report(y_true, y_pred, zero_division=0))
    logger.info("acc=%.4f  prec=%.4f  recall=%.4f  f1=%.4f", acc, prec, rec, f1)

    # Análisis de under/over-triage (clave en triaje clínico)
    df["urg_real"] = df["etiqueta"].map(_NIVELES.index)
    df["urg_pred"] = df["prediccion"].map(_NIVELES.index)
    df["error"]    = df["urg_pred"] - df["urg_real"]

    under_triage = int((df["error"] > 0).sum())   # predijo MENOS urgente → peligroso
    over_triage  = int((df["error"] < 0).sum())   # predijo MÁS urgente → no peligroso
    correctos    = int((df["error"] == 0).sum())

    # Valoración por caso (formato proyecto.txt)
    valoracion_por_caso = []
    for _, fila in df.iterrows():
        if fila["error"] == 0:
            etiq, score_val = "CORRECTO", 1.0
        elif fila["error"] > 0:
            etiq = "UNDER_TRIAGE"
            score_val = max(0.0, 1.0 - 0.25 * fila["error"])
        else:
            etiq = "OVER_TRIAGE"
            score_val = max(0.5, 1.0 - 0.15 * abs(fila["error"]))
        valoracion_por_caso.append({
            "guid":         fila["guid"],
            "etiqueta":     fila["etiqueta"],
            "prediccion":   fila["prediccion"],
            "valoracion":   etiq,
            "score":        round(score_val, 2),
            "error_niveles": int(fila["error"]),
        })

    # Gráfica matriz de confusión Fase 2
    labels = sorted(set(y_true) | set(y_pred))
    cm     = confusion_matrix(y_true, y_pred, labels=labels)
    fig, ax = plt.subplots(figsize=(6, 5))
    ConfusionMatrixDisplay(cm, display_labels=labels).plot(
        ax=ax, cmap="Blues", colorbar=False
    )
    ax.set_title(f"Matriz de confusión — Fase 2 ({len(df)} casos)")
    plt.tight_layout()
    buf = io.BytesIO(); fig.savefig(buf, format="png", dpi=100); plt.close(fig)
    subir_bytes(
        BUCKET_MODELOS, "evaluacion_fase2_confusion.png",
        buf.getvalue(), content_type="image/png",
    )
    logger.info("✓ evaluacion_fase2_confusion.png subido a MinIO")

    # JSON con la valoración completa
    valoracion = {
        "fecha_evaluacion": datetime.now().isoformat(),
        "n_casos_total":    len(registros),
        "n_casos_validos":  len(df),
        "metricas_globales": {
            "accuracy":        round(acc,  4),
            "precision_macro": round(prec, 4),
            "recall_macro":    round(rec,  4),
            "f1_macro":        round(f1,   4),
        },
        "triaje_clinico": {
            "correctos":    correctos,
            "under_triage": under_triage,
            "over_triage":  over_triage,
            "tasa_under":   round(under_triage / len(df), 4),
            "tasa_over":    round(over_triage  / len(df), 4),
        },
        "por_caso": valoracion_por_caso,
    }
    subir_bytes(
        BUCKET_MODELOS, "evaluacion_fase2.json",
        json.dumps(valoracion, ensure_ascii=False, indent=2).encode("utf-8"),
        content_type="application/json",
    )
    logger.info("✓ evaluacion_fase2.json subido a MinIO")

    # Marcar estado EVALUACION_COMPLETADA
    for guid in df["guid"]:
        try:
            db.actualizar_entrevista(guid, estado="EVALUACION_COMPLETADA")
        except Exception as exc:
            logger.warning("No se pudo actualizar estado de %s: %s", guid, exc)

    logger.info(
        "✓ Evaluación completada: acc=%.2f%%  recall=%.2f%%  "
        "correctos=%d  under-triage=%d  over-triage=%d",
        acc * 100, rec * 100, correctos, under_triage, over_triage,
    )


with DAG(
    dag_id="dag_evaluation",
    description="Fase 2 — Compara etiqueta LLM vs prediccion_prueba, calcula valoración",
    start_date=datetime(2026, 1, 1),
    schedule=None,
    catchup=False,
    default_args={"retries": 1, "retry_delay": timedelta(seconds=30)},
    tags=["fase2"],
) as dag:

    PythonOperator(
        task_id="evaluar_predicciones",
        python_callable=_evaluar,
    )
