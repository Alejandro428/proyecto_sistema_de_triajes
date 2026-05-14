"""
DAG Fase 1 - Paso 3: Entrenamiento del modelo ML
Lee dataset_entrenamiento.csv de MinIO, entrena un RandomForest,
genera 3 gráficas básicas y guarda el modelo en MinIO.
Se lanza automáticamente cuando termina dag_llm_enrichment.
"""

import io
import json
import os
from datetime import datetime

import joblib
import matplotlib
matplotlib.use("Agg")  # sin pantalla, necesario en Docker
import matplotlib.pyplot as plt
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report, ConfusionMatrixDisplay, confusion_matrix
from sklearn.preprocessing import MultiLabelBinarizer

from airflow import DAG
from airflow.operators.python import PythonOperator

from pipeline.db import DatabaseService
from pipeline.minio_client import descargar_bytes, subir_bytes, BUCKET_MODELOS, BUCKET_DATASETS

DATABASE_URL = os.environ["DATABASE_URL"]


def _entrenar(**context):
    db = DatabaseService(DATABASE_URL)

    csv_bytes = descargar_bytes(BUCKET_DATASETS, "dataset_entrenamiento.csv")
    df = pd.read_csv(io.BytesIO(csv_bytes))

    if len(df) < 10:
        raise ValueError(f"Solo hay {len(df)} casos. Necesitamos más para entrenar.")

    print(f"→ Entrenando con {len(df)} casos")
    print(f"  Distribución de etiquetas:\n{df['etiqueta'].value_counts().to_string()}")

    # Parsear listas almacenadas como strings JSON
    df["entidades_normalizadas"] = df["entidades_normalizadas"].apply(
        lambda x: json.loads(x) if isinstance(x, str) else []
    )

    # Features: términos clínicos binarizados + score de ansiedad
    mlb = MultiLabelBinarizer()
    X_terminos = pd.DataFrame(
        mlb.fit_transform(df["entidades_normalizadas"]),
        columns=mlb.classes_,
    )
    X = pd.concat([
        X_terminos,
        df["score_ansiedad"].fillna(0).reset_index(drop=True),
    ], axis=1)

    # Target: etiqueta Manchester (C1-C5)
    y = df["etiqueta"]

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42
    )

    clf = RandomForestClassifier(
        n_estimators=100, class_weight="balanced", random_state=42
    )
    clf.fit(X_train, y_train)
    y_pred = clf.predict(X_test)

    print("\n--- Resultados en test (20%) ---")
    print(classification_report(y_test, y_pred))

    # Gráfica 1: distribución de clases en el dataset
    fig, ax = plt.subplots(figsize=(7, 4))
    y.value_counts().sort_index().plot(kind="bar", ax=ax, color="#4e79a7", edgecolor="black")
    ax.set_title("Distribución de niveles Manchester en el dataset")
    ax.set_xlabel("Nivel de urgencia (etiqueta)")
    ax.set_ylabel("Número de casos")
    ax.tick_params(axis="x", rotation=0)
    plt.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100)
    plt.close(fig)
    subir_bytes(BUCKET_MODELOS, "grafica_distribucion.png", buf.getvalue())
    print("✓ grafica_distribucion.png")

    # Gráfica 2: matriz de confusión
    labels = sorted(y.unique())
    cm = confusion_matrix(y_test, y_pred, labels=labels)
    fig, ax = plt.subplots(figsize=(6, 5))
    ConfusionMatrixDisplay(confusion_matrix=cm, display_labels=labels).plot(
        ax=ax, colorbar=False, cmap="Blues"
    )
    ax.set_title("Matriz de confusión (test 20%)")
    plt.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100)
    plt.close(fig)
    subir_bytes(BUCKET_MODELOS, "grafica_confusion.png", buf.getvalue())
    print("✓ grafica_confusion.png")

    # Gráfica 3: top 15 términos clínicos más importantes
    importancias = pd.Series(clf.feature_importances_, index=X.columns)
    top15 = importancias.nlargest(15).sort_values()
    fig, ax = plt.subplots(figsize=(8, 5))
    top15.plot(kind="barh", ax=ax, color="#59a14f", edgecolor="black")
    ax.set_title("Top 15 términos clínicos más relevantes para el modelo")
    ax.set_xlabel("Importancia")
    plt.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100)
    plt.close(fig)
    subir_bytes(BUCKET_MODELOS, "grafica_importancia.png", buf.getvalue())
    print("✓ grafica_importancia.png")

    # Guardar modelo + binarizador juntos (necesario para predicción en Fase 2)
    buf_modelo = io.BytesIO()
    joblib.dump({"modelo": clf, "binarizador": mlb}, buf_modelo)
    url_modelo = subir_bytes(BUCKET_MODELOS, "modelo_triageia.pkl", buf_modelo.getvalue())
    print(f"✓ modelo_triageia.pkl guardado en {url_modelo}")

    # Actualizar Entrevista
    now = datetime.now()
    for guid in df["guid"].tolist():
        db.actualizar_entrevista(
            guid,
            estado="MODELO_ENTRENADO",
            inicio_entrenamiento=now,
            fin_entrenamiento=now,
            url_dataset_generado=f"minio://{BUCKET_DATASETS}/dataset_entrenamiento.csv",
            url_modelo_entrenado=url_modelo,
        )

    print(f"\n✓ {len(df)} entrevistas actualizadas a MODELO_ENTRENADO")
    print("✓ Fase 1 completada")


with DAG(
    dag_id="dag_model_training",
    description="Fase 1 - Paso 3: dataset_entrenamiento.csv → RandomForest + gráficas",
    start_date=datetime(2026, 1, 1),
    schedule=None,
    catchup=False,
    tags=["fase1"],
) as dag:
    PythonOperator(
        task_id="entrenar_modelo",
        python_callable=_entrenar,
    )
