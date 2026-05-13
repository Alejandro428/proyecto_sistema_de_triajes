"""
DAG Fase 1 - Paso 3: Entrenamiento del modelo ML
Lee todos los casos enriquecidos de Postgres, entrena un RandomForest,
guarda el modelo y el master.csv en MinIO.
Trigger: manual, después de dag_llm_enrichment.
"""

import io
import json
import os
from datetime import datetime

import joblib
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report
from sklearn.preprocessing import MultiLabelBinarizer

from airflow import DAG
from airflow.operators.python import PythonOperator

from pipeline.db import DatabaseService
from pipeline.minio_client import subir_bytes, BUCKET_MODELOS, BUCKET_DATASETS

DATABASE_URL = os.environ["DATABASE_URL"]


def _entrenar(**context):
    db    = DatabaseService(DATABASE_URL)
    casos = db.obtener_todos_enriquecidos()

    if len(casos) < 10:
        raise ValueError(f"Solo hay {len(casos)} casos enriquecidos. Necesitamos más para entrenar.")

    print(f"→ Entrenando con {len(casos)} casos")
    df = pd.DataFrame(casos)

    # Binarizar los términos clínicos (lista de strings → columnas 0/1)
    mlb = MultiLabelBinarizer()
    terminos = df["terminos_clinicos"].apply(lambda x: x if isinstance(x, list) else [])
    X_terminos = pd.DataFrame(
        mlb.fit_transform(terminos),
        columns=mlb.classes_,
    )

    X = pd.concat([
        X_terminos,
        df["nivel_ansiedad"].fillna(0).reset_index(drop=True),
    ], axis=1)
    y = df["nivel_urgencia"]

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )

    clf = RandomForestClassifier(
        n_estimators=100, class_weight="balanced", random_state=42
    )
    clf.fit(X_train, y_train)

    report = classification_report(y_test, clf.predict(X_test))
    print(report)

    # Guardar modelo en MinIO
    buf_modelo = io.BytesIO()
    joblib.dump({"modelo": clf, "binarizador": mlb}, buf_modelo)
    url_modelo = subir_bytes(BUCKET_MODELOS, "modelo_triageia.pkl", buf_modelo.getvalue())

    # Guardar master.csv en MinIO
    buf_csv = io.BytesIO(df.to_csv(index=False).encode("utf-8"))
    url_dataset = subir_bytes(BUCKET_DATASETS, "master.csv", buf_csv.getvalue())

    # Registrar timestamps de entrenamiento en todas las entrevistas
    now = datetime.now()
    for caso in casos:
        db.actualizar_entrevista(
            caso["guid_entrevista"],
            estado="MODELO_ENTRENADO",
            inicio_entrenamiento=now,
            fin_entrenamiento=now,
            url_dataset_generado=url_dataset,
            url_modelo_entrenado=url_modelo,
        )

    print(f"✓ Modelo guardado en {url_modelo}")
    print(f"✓ Dataset guardado en {url_dataset}")


with DAG(
    dag_id="dag_model_training",
    description="Fase 1 - Paso 3: Entrena RandomForest → modelo.pkl en MinIO",
    start_date=datetime(2026, 1, 1),
    schedule=None,
    catchup=False,
    tags=["fase1"],
) as dag:
    PythonOperator(
        task_id="entrenar_modelo",
        python_callable=_entrenar,
    )
