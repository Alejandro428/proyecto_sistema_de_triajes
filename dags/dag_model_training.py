"""
DAG Fase 1 — Paso 3: Entrenamiento del modelo ML
Lee dataset_entrenamiento.csv de MinIO, entrena TF-IDF + Logistic Regression,
genera 3 gráficas y guarda el modelo en MinIO.
Se lanza automáticamente cuando termina dag_llm_enrichment.
"""

import io
import json
import logging
from collections import Counter
from datetime import datetime, timedelta

import joblib
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import scipy.sparse as sp
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    ConfusionMatrixDisplay,
    accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
)
from sklearn.model_selection import train_test_split

from airflow import DAG
from airflow.operators.python import PythonOperator

from pipeline.config import DATABASE_URL
from pipeline.db import DatabaseService
from pipeline.minio_client import (
    BUCKET_DATASETS, BUCKET_ENRIQUECIDOS, BUCKET_MODELOS,
    descargar_bytes, subir_bytes,
)

logger = logging.getLogger(__name__)

NIVELES_MANCHESTER = ["C1", "C2", "C3", "C4", "C5"]
COLORES_MANCHESTER = {"C1": "#B71C1C", "C2": "#E65100", "C3": "#F57F17", "C4": "#1B5E20", "C5": "#0D47A1"}


def _preparar_features(df: pd.DataFrame, tfidf: TfidfVectorizer | None = None):
    """Convierte entidades normalizadas + score_ansiedad en matriz densa."""
    textos = [" ".join(ents) for ents in df["entidades_normalizadas"]]
    scores = df["score_ansiedad"].fillna(0).values.reshape(-1, 1)

    if tfidf is None:
        tfidf = TfidfVectorizer(ngram_range=(1, 2), min_df=1, sublinear_tf=True)
        X_tfidf = tfidf.fit_transform(textos)
    else:
        X_tfidf = tfidf.transform(textos)

    X = np.hstack([X_tfidf.toarray(), scores])
    return X, tfidf


def _aplicar_smote(X_train, y_train):
    """Aplica SMOTE solo si todas las clases tienen ≥ 2 muestras."""
    from imblearn.over_sampling import SMOTE

    conteo = Counter(y_train)
    min_clase = min(conteo.values())
    if min_clase < 2:
        logger.warning("Clase con 1 muestra detectada (%s). Omitiendo SMOTE.", conteo)
        return X_train, y_train

    k = min(5, min_clase - 1)
    try:
        sm = SMOTE(random_state=42, k_neighbors=k)
        X_res, y_res = sm.fit_resample(X_train, y_train)
        logger.info("SMOTE aplicado: %d → %d muestras (k_neighbors=%d)", len(y_train), len(y_res), k)
        return X_res, y_res
    except Exception as exc:
        logger.warning("SMOTE falló (%s). Usando datos originales.", exc)
        return X_train, y_train


def _entrenar(**context):
    db = DatabaseService(DATABASE_URL)

    csv_bytes = descargar_bytes(BUCKET_DATASETS, "dataset_entrenamiento.csv")
    df = pd.read_csv(io.BytesIO(csv_bytes))

    if len(df) < 10:
        raise ValueError(f"Solo hay {len(df)} casos. Se necesitan más para entrenar.")

    df["entidades_normalizadas"] = df["entidades_normalizadas"].apply(
        lambda x: json.loads(x) if isinstance(x, str) else []
    )

    y = df["etiqueta"]
    logger.info("→ Entrenando con %d casos", len(df))
    logger.info("Distribución de etiquetas:\n%s", y.value_counts().sort_index().to_string())

    X, tfidf = _preparar_features(df)

    try:
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, stratify=y, random_state=42
        )
    except ValueError:
        logger.warning("stratify falló (clases con 1 muestra). Usando split sin stratify.")
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42
        )

    X_train_res, y_train_res = _aplicar_smote(X_train, y_train)

    t_inicio = datetime.now()
    clf = LogisticRegression(
        class_weight="balanced",
        C=1.0,
        max_iter=500,
        solver="lbfgs",
        random_state=42,
    )
    clf.fit(X_train_res, y_train_res)
    t_fin = datetime.now()
    dur_train = (t_fin - t_inicio).total_seconds()

    y_pred = clf.predict(X_test)

    acc  = accuracy_score(y_test, y_pred)
    prec = precision_score(y_test, y_pred, average="macro", zero_division=0)
    rec  = recall_score(y_test, y_pred, average="macro", zero_division=0)
    f1   = f1_score(y_test, y_pred, average="macro", zero_division=0)

    logger.info("--- Métricas en test (20%%) ---")
    logger.info(classification_report(y_test, y_pred, zero_division=0))
    logger.info("Accuracy=%.4f  Precision=%.4f  Recall=%.4f  F1=%.4f", acc, prec, rec, f1)
    logger.info("Tiempo de entrenamiento: %.2fs", dur_train)

    # ── Gráfica 1: distribución de clases ────────────────────────────────────
    fig, ax = plt.subplots(figsize=(7, 4))
    dist = y.value_counts().sort_index()
    colores = [COLORES_MANCHESTER.get(n, "#888") for n in dist.index]
    dist.plot(kind="bar", ax=ax, color=colores, edgecolor="black")
    ax.set_title("Distribución de niveles Manchester en el dataset")
    ax.set_xlabel("Nivel de urgencia")
    ax.set_ylabel("Número de casos")
    ax.tick_params(axis="x", rotation=0)
    for i, v in enumerate(dist):
        ax.text(i, v + 0.5, str(v), ha="center", fontsize=9)
    plt.tight_layout()
    buf = io.BytesIO(); fig.savefig(buf, format="png", dpi=100); plt.close(fig)
    subir_bytes(BUCKET_MODELOS, "grafica_distribucion.png", buf.getvalue(), content_type="image/png")
    logger.info("✓ grafica_distribucion.png")

    # ── Gráfica 2: matriz de confusión ───────────────────────────────────────
    labels = sorted(y.unique())
    cm = confusion_matrix(y_test, y_pred, labels=labels)
    fig, ax = plt.subplots(figsize=(6, 5))
    ConfusionMatrixDisplay(confusion_matrix=cm, display_labels=labels).plot(
        ax=ax, colorbar=False, cmap="Blues"
    )
    ax.set_title("Matriz de confusión — TF-IDF + LR (test 20%)")
    plt.tight_layout()
    buf = io.BytesIO(); fig.savefig(buf, format="png", dpi=100); plt.close(fig)
    subir_bytes(BUCKET_MODELOS, "grafica_confusion.png", buf.getvalue(), content_type="image/png")
    logger.info("✓ grafica_confusion.png")

    # ── Gráfica 3: coeficientes TF-IDF por nivel Manchester ──────────────────
    nombres_features = tfidf.get_feature_names_out().tolist() + ["score_ansiedad"]
    clases_modelo    = list(clf.classes_)
    coef_matrix      = clf.coef_  # shape (n_clases, n_features)

    # Top 15 términos por coeficiente máximo absoluto entre todas las clases
    max_abs_coef = np.abs(coef_matrix).max(axis=0)
    top_idx = np.argsort(max_abs_coef)[-15:]
    top_names = [nombres_features[i] for i in top_idx]

    # Coeficientes de esos términos por clase (para el gráfico de barras agrupadas)
    top_coefs = coef_matrix[:, top_idx]  # (n_clases, 15)

    fig, ax = plt.subplots(figsize=(10, 6))
    x = np.arange(len(top_names))
    ancho = 0.15
    for j, clase in enumerate(clases_modelo):
        offset = (j - len(clases_modelo) / 2) * ancho
        color  = COLORES_MANCHESTER.get(clase, "#888")
        ax.bar(x + offset, top_coefs[j], ancho, label=clase, color=color, alpha=0.85)

    ax.set_xticks(x)
    ax.set_xticklabels(top_names, rotation=35, ha="right", fontsize=8)
    ax.set_title("Top 15 términos clínicos — coeficientes LR por nivel Manchester")
    ax.set_ylabel("Coeficiente (positivo = favorece el nivel)")
    ax.axhline(0, color="black", linewidth=0.8)
    ax.legend(title="Nivel", loc="upper left")
    plt.tight_layout()
    buf = io.BytesIO(); fig.savefig(buf, format="png", dpi=100); plt.close(fig)
    subir_bytes(BUCKET_MODELOS, "grafica_importancia.png", buf.getvalue(), content_type="image/png")
    logger.info("✓ grafica_importancia.png")

    # ── Guardar artefacto ────────────────────────────────────────────────────
    buf_modelo = io.BytesIO()
    joblib.dump({
        "vectorizador": tfidf,
        "modelo":       clf,
        "metricas": {
            "accuracy":        round(acc,  4),
            "precision_macro": round(prec, 4),
            "recall_macro":    round(rec,  4),
            "f1_macro":        round(f1,   4),
            "train_segundos":  round(dur_train, 2),
            "n_casos":         len(df),
        },
    }, buf_modelo)
    url_modelo = subir_bytes(
        BUCKET_MODELOS, "modelo_triageia.pkl", buf_modelo.getvalue()
    )
    logger.info("✓ modelo_triageia.pkl → %s", url_modelo)

    # ── Rellenar prediccion_entrenada en TODOS los casos ─────────────────────
    logger.info("→ Generando prediccion_entrenada para %d casos...", len(df))
    X_todos, _ = _preparar_features(df, tfidf)
    predicciones = clf.predict(X_todos)

    registros_csv = []
    for i, (_, fila) in enumerate(df.iterrows()):
        guid_caso   = fila["guid"]
        pred        = predicciones[i]

        # Actualizar JSON en MinIO
        try:
            raw_json  = descargar_bytes(BUCKET_ENRIQUECIDOS, f"{guid_caso}.json")
            enriquecido = json.loads(raw_json)
            enriquecido["prediccion_entrenada"] = pred
            data = json.dumps(enriquecido, ensure_ascii=False).encode("utf-8")
            subir_bytes(BUCKET_ENRIQUECIDOS, f"{guid_caso}.json", data, content_type="application/json")
        except Exception as exc:
            logger.warning("No se pudo actualizar JSON de %s: %s", guid_caso, exc)

        fila_dict = fila.to_dict()
        fila_dict["prediccion_entrenada"] = pred
        registros_csv.append(fila_dict)

    # Rebuild dataset_entrenamiento.csv con predicciones rellenas
    df_final = pd.DataFrame(registros_csv)
    for col in ("entidades", "entidades_normalizadas"):
        if col in df_final.columns:
            df_final[col] = df_final[col].apply(
                lambda x: json.dumps(x, ensure_ascii=False) if isinstance(x, list) else x
            )
    csv_final = df_final.to_csv(index=False).encode("utf-8")
    subir_bytes(BUCKET_DATASETS, "dataset_entrenamiento.csv", csv_final, content_type="text/csv")
    logger.info("✓ dataset_entrenamiento.csv actualizado con predicciones")

    # ── Actualizar entrevistas ───────────────────────────────────────────────
    fin_pipeline = datetime.now()
    for guid in df["guid"].tolist():
        db.actualizar_entrevista(
            guid,
            estado="MODELO_ENTRENADO",
            fin_solicitud=fin_pipeline,
            inicio_entrenamiento=t_inicio,
            fin_entrenamiento=t_fin,
            url_dataset_generado=f"minio://{BUCKET_DATASETS}/dataset_entrenamiento.csv",
            url_modelo_entrenado=url_modelo,
        )

    logger.info("✓ %d entrevistas → MODELO_ENTRENADO", len(df))
    logger.info("✓ Fase 1 completada — TF-IDF + Logistic Regression")


with DAG(
    dag_id="dag_model_training",
    description="Fase 1 — Paso 3: TF-IDF + Logistic Regression sobre entidades normalizadas",
    start_date=datetime(2026, 1, 1),
    schedule=None,
    catchup=False,
    default_args={"retries": 1, "retry_delay": timedelta(seconds=30)},
    tags=["fase1"],
) as dag:
    PythonOperator(
        task_id="entrenar_modelo",
        python_callable=_entrenar,
    )
