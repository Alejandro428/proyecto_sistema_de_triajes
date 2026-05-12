"""
Fase 1 - Paso 2/3: Etiquetado con LLM y generación del master.csv
Lee conversaciones.csv, llama a Mistral por cada caso,
guarda en PostgreSQL y acumula el master.csv incremetalmente.
"""

import json
import os
import re
import sys
from pathlib import Path

import pandas as pd

from fase1.prompts import SYSTEM_PROMPT


def extraer_json(texto: str) -> dict:
    """Extrae el JSON de la respuesta del LLM aunque venga con texto extra."""
    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", texto, re.DOTALL)
    if match:
        return json.loads(match.group())
    raise ValueError(f"No se pudo extraer JSON de la respuesta: {texto[:300]}")


def procesar_dataset(
    llm,
    db,
    conversaciones_csv: Path,
    master_csv: Path,
) -> None:
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
            respuesta_raw = llm.procesar_caso(SYSTEM_PROMPT, row["texto_completo"])
            datos = extraer_json(respuesta_raw)

            caso = {
                "id_caso":               caso_id,
                "categoria":             row["categoria"],
                "origen":                row["origen"],
                "texto_original":        row["texto_completo"],
                "resumen_es":            datos.get("resumen_es", ""),
                "entidades_extraidas":   datos.get("entidades_extraidas", []),
                "entidades_normalizadas": datos.get("entidades_normalizadas", []),
                "triage_real":           datos.get("triage_real", ""),
                "justificacion":         datos.get("justificacion", ""),
                "score_ansiedad":        float(datos.get("score_ansiedad", 0.0)),
            }

            db.insertar_caso(caso)
            _guardar_fila_csv(caso, master_csv)

            procesados += 1
            print(f"✓ [{i+1}/{total}] {caso_id} → {caso['triage_real']}  ansiedad={caso['score_ansiedad']}")

        except Exception as e:
            errores += 1
            print(f"✗ [{i+1}/{total}] {caso_id}: {e}", file=sys.stderr)

    print(f"\nFinalizado: {procesados} procesados, {errores} errores.")
    print(f"Master CSV en: {master_csv}")


def _guardar_fila_csv(caso: dict, master_csv: Path) -> None:
    """Añade una fila al master.csv de forma incremental."""
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
