"""
DAG Fase 1 — Paso 2: Enriquecimiento LLM
Lee conversaciones.csv de MinIO, llama a Mistral por cada caso,
guarda un JSON por caso (reanudable si falla), y genera
dataset_entrenamiento.csv para entrenar el modelo en Orange.
Se lanza automáticamente cuando termina dag_ingestion.

Nota: este DAG NO guarda nada en Postgres. La idempotencia se basa en
`json_existe(guid)` sobre el bucket de MinIO `enriquecidos`. Reanudable
sin pérdida: los casos ya enriquecidos se saltan.
"""

import io
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta

import pandas as pd
from airflow import DAG
from airflow.operators.python import PythonOperator

from pipeline.config import MISTRAL_API_KEY
from pipeline.diccionario_clinico import normalizar_entidades
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

def _fix_string_newlines(text: str) -> str:
    """Escapa saltos de línea y tabs crudos dentro de strings JSON.

    Mistral ocasionalmente genera strings multi-línea sin escapar los \\n,
    lo que produce JSON estructuralmente inválido.
    """
    result = []
    in_string = False
    i = 0
    while i < len(text):
        c = text[i]
        if in_string:
            if c == "\\":
                result.append(c)
                if i + 1 < len(text):
                    result.append(text[i + 1])
                    i += 2
                    continue
            elif c == '"':
                in_string = False
                result.append(c)
            elif c == "\n":
                result.append("\\n")
            elif c == "\r":
                result.append("\\r")
            elif c == "\t":
                result.append("\\t")
            else:
                result.append(c)
        else:
            if c == '"':
                in_string = True
            result.append(c)
        i += 1
    return "".join(result)


def _extraer_json(texto: str) -> dict:
    """Intenta parsear JSON directo; si falla limpia markdown y reintenta."""
    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        pass
    # Mistral a veces envuelve en bloques ```json … ```
    sin_md = re.sub(r"```(?:json)?", "", texto).strip()
    try:
        return json.loads(sin_md)
    except json.JSONDecodeError:
        pass
    # Mistral a veces pone saltos de línea crudos dentro de strings JSON
    try:
        return json.loads(_fix_string_newlines(sin_md))
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", sin_md, re.DOTALL)
    if match:
        try:
            return json.loads(_fix_string_newlines(match.group()))
        except json.JSONDecodeError:
            pass
    raise ValueError(f"JSON inválido en respuesta LLM: {texto[:200]}")


def _validar_datos(datos: dict, guid: str) -> None:
    """Lanza ValueError si faltan campos obligatorios o el nivel es inválido.

    Nota: `entidades_normalizadas` PUEDE ser una lista vacía (caso C5
    rutinario sin síntomas agudos), pero la clave debe estar presente.
    """
    if "entidades_normalizadas" not in datos:
        raise ValueError(f"{guid}: campo requerido ausente: 'entidades_normalizadas'")
    if not isinstance(datos["entidades_normalizadas"], list):
        raise ValueError(f"{guid}: 'entidades_normalizadas' debe ser lista")
    for campo in ("triage_real", "score_ansiedad"):
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
            # Llamada LLM (extrae entidades, normaliza, etiqueta y calcula score
            # en una sola llamada)
            datos, duracion_llm, reintentos = _llamar_con_retry(llm, texto.strip())
            _validar_datos(datos, guid)

            # Calcular la entidad clínica principal (la más grave) aplicando
            # el diccionario clínico. Se guarda en el JSON para que el detalle
            # del historial pueda mostrarla sin reprocesar.
            ents_raw  = datos.get("entidades_normalizadas", []) or []
            ents_norm = normalizar_entidades(ents_raw, max_n=1)
            entidad_principal = ents_norm[0] if ents_norm else "Sin_entidad"

            enriquecido = {
                "guid":                   guid,
                "origen":                 origen,
                "categoria":              fila["categoria"],
                "texto":                  texto,
                "resumen":                datos.get("resumen_es", ""),
                "entidades":              datos.get("entidades_extraidas", []),
                "entidades_normalizadas": datos.get("entidades_normalizadas", []),
                "entidad_principal":      entidad_principal,
                "etiqueta":               datos.get("triage_real", ""),
                "razonamiento":           datos.get("justificacion", ""),
                "score_ansiedad":         _parsear_score(datos.get("score_ansiedad", 0.0)),
                "prediccion_entrenada":   "",
            }
            subir_json(guid, enriquecido)

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
            logger.error("✗ %s: %s", guid, exc)

    # ---- Construir dataset_entrenamiento.csv ----
    # Aplica el diccionario clínico (hasta 5 entidades estándar por caso).
    # Las entidades se guardan como TEXTO (separadas por espacio); el
    # one-hot/multi-hot lo genera Orange con Corpus → Bag of Words Binary.
    # Se incluyen todas las etiquetas C1-C5.
    logger.info("→ Construyendo dataset_entrenamiento.csv...")

    registros = []
    for _, fila in df_conv.iterrows():
        try:
            j = descargar_json(fila["guid"])
            ents_raw  = j.get("entidades_normalizadas", []) or []
            ents_norm = normalizar_entidades(ents_raw, max_n=5)
            registros.append({
                "entidades_normalizadas": " ".join(ents_norm) if ents_norm else "Sin_Sintomas",
                "n_sintomas":             len(ents_norm),
                "categoria":              j.get("categoria", "") or "GEN",
                "score_ansiedad":         float(j.get("score_ansiedad", 0.0) or 0.0),
                "etiqueta":               j.get("etiqueta", ""),
            })
        except Exception as exc:
            logger.warning("Sin JSON para %s: %s", fila["guid"], exc)

    df_train = pd.DataFrame(registros)
    df_train = df_train[df_train["etiqueta"].isin(["C1", "C2", "C3", "C4", "C5"])]

    csv_bytes = df_train.to_csv(index=False).encode("utf-8")

    local_dir = "/opt/airflow/data/processed"
    os.makedirs(local_dir, exist_ok=True)
    local_path = os.path.join(local_dir, "dataset_entrenamiento.csv")
    with open(local_path, "wb") as f:
        f.write(csv_bytes)
    subir_bytes(BUCKET_DATASETS, "dataset_entrenamiento.csv", csv_bytes, content_type="text/csv")

    logger.info(
        "✓ dataset_entrenamiento.csv (%d filas, %d columnas) → MinIO + %s",
        len(df_train), df_train.shape[1], local_path,
    )

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
    # Nota: el entrenamiento del modelo ya no se hace en Airflow.
    # Se hace en Orange Data Mining (workflow externo) usando
    # data/processed/dataset_entrenamiento.csv:
    #   - entidades_normalizadas → Corpus → Bag of Words Binary (multi-hot)
    #   - categoria              → Continuize (one-hot)
    #   - n_sintomas, score_ansiedad → numéricas
    # El modelo .pkcls resultante se guarda en ./models/randomforest_model.pkcls
    # (montado en /app/models dentro del contenedor de la API).
