"""
Fase 1 - Paso 2: Etiquetado con LLM y generación del master.csv
Lee conversaciones.csv, llama a Mistral por cada caso,
guarda en PostgreSQL y acumula el master.csv incrementalmente.
"""

import json
import os
import re
import sys
import time
from pathlib import Path

import pandas as pd

from fase1.prompts import SYSTEM_PROMPT


_SLEEP_ENTRE_LLAMADAS = 1.5
_REINTENTOS_MAX       = 6
_ESPERA_INICIAL_429   = 15


def extraer_json(texto: str) -> dict:
    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", texto, re.DOTALL)
    if match:
        return json.loads(match.group())
    raise ValueError(f"No se pudo extraer JSON de la respuesta: {texto[:300]}")


def _llamar_con_retry(llm, system_prompt: str, texto: str) -> str:
    espera = _ESPERA_INICIAL_429
    for intento in range(_REINTENTOS_MAX):
        try:
            resultado = llm.procesar_caso(system_prompt, texto)
            time.sleep(_SLEEP_ENTRE_LLAMADAS)
            return resultado
        except Exception as e:
            if "429" in str(e) and intento < _REINTENTOS_MAX - 1:
                print(f"  ⏳ Rate limit, esperando {espera}s (intento {intento+1}/{_REINTENTOS_MAX})...")
                time.sleep(espera)
                espera = min(espera * 2, 120)
            else:
                raise
    raise RuntimeError("Máximo de reintentos alcanzado")


def procesar_dataset(llm, db, conversaciones_csv: Path, master_csv: Path) -> None:
    df = pd.read_csv(conversaciones_csv)
    total = len(df)
    procesados = 0
    errores = 0

    for i, row in df.iterrows():
        caso_id = row["id_caso"]

        if db.existe_caso(caso_id):
            print(f"⏭  [{i+1}/{total}] {caso_id} ya procesado, saltando")
            continue

        try:
            respuesta_raw = _llamar_con_retry(llm, SYSTEM_PROMPT, row["transcripcion"])
            datos = extraer_json(respuesta_raw)

            caso = {
                "id_caso":            caso_id,
                "categoria":          row["categoria"],
                "transcripcion":      row["transcripcion"],
                "resumen":            datos.get("resumen_es", ""),
                "sintomas_detectados": datos.get("entidades_extraidas", []),
                "terminos_clinicos":  datos.get("entidades_normalizadas", []),
                "nivel_urgencia":     datos.get("triage_real", ""),
                "razonamiento":       datos.get("justificacion", ""),
                "nivel_ansiedad":     float(datos.get("score_ansiedad", 0.0)),
            }

            db.insertar_caso(caso)
            _guardar_fila_csv(caso, master_csv)

            procesados += 1
            print(f"✓ [{i+1}/{total}] {caso_id} → {caso['nivel_urgencia']}  ansiedad={caso['nivel_ansiedad']}")

        except Exception as e:
            errores += 1
            print(f"✗ [{i+1}/{total}] {caso_id}: {e}", file=sys.stderr)

    print(f"\nFinalizado: {procesados} procesados, {errores} errores.")
    print(f"Master CSV en: {master_csv}")


def _guardar_fila_csv(caso: dict, master_csv: Path) -> None:
    master_csv.parent.mkdir(parents=True, exist_ok=True)
    fila = pd.DataFrame([caso])
    if not master_csv.exists():
        fila.to_csv(master_csv, index=False, encoding="utf-8")
    else:
        fila.to_csv(master_csv, index=False, encoding="utf-8", mode="a", header=False)


if __name__ == "__main__":
    from services.llm import LLMService
    from services.db import DatabaseService

    api_key      = os.environ["MISTRAL_API_KEY"]
    database_url = os.environ["DATABASE_URL"]
    data_dir     = Path(os.getenv("DATA_DIR", "/app/data"))

    llm = LLMService(api_key)
    db  = DatabaseService(database_url)

    conversaciones_csv = data_dir / "processed" / "conversaciones.csv"
    master_csv         = data_dir / "processed" / "master.csv"

    if not conversaciones_csv.exists():
        print("conversaciones.csv no encontrado. Ejecuta primero el parser.")
        sys.exit(1)

    procesar_dataset(llm, db, conversaciones_csv, master_csv)
