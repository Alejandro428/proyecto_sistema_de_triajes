import json
import re
from datetime import datetime
from io import BytesIO

import httpx
import joblib
import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import (
    DATABASE_URL, MINIO_ENDPOINT, MINIO_ACCESS_KEY, MINIO_SECRET_KEY,
    MISTRAL_API_KEY,
)
from services.db import DatabaseService
from services.minio_service import MinIOService

router = APIRouter(prefix="/fase3", tags=["Fase 2 — Predicción"])

MISTRAL_API_URL = "https://api.mistral.ai/v1/chat/completions"

DEFAULT_SYSTEM_PROMPT = """Eres un médico experto en urgencias hospitalarias especializado en el Protocolo de Triaje Manchester.
Tu tarea es analizar transcripciones de entrevistas clínicas entre médico y paciente, y extraer información estructurada para un sistema de triaje automatizado.

## PROTOCOLO MANCHESTER
- C1 (Rojo, 0 min): Emergencia vital inmediata. Parada cardiorrespiratoria, síncope, obstrucción vía aérea.
- C2 (Naranja, 10 min): Muy urgente. Dolor torácico opresivo, disnea aguda, crisis asmática severa, posible infarto.
- C3 (Amarillo, 60 min): Urgente. Fiebre alta, sibilancias moderadas, dolor moderado, vómitos persistentes.
- C4 (Verde, 120 min): Menos urgente. Edema sin signos de alarma, dolor leve, síntomas leves controlados.
- C5 (Azul, 240 min): No urgente. Consulta rutinaria, síntomas mínimos de larga evolución.

## FORMATO DE SALIDA
Devuelve ÚNICAMENTE un JSON válido, sin texto adicional:
{
  "resumen_es": "resumen breve en español (2-3 frases)",
  "entidades_extraidas": ["síntoma tal como aparece en el texto"],
  "entidades_normalizadas": ["término clínico estándar en español"],
  "triage_real": "C1|C2|C3|C4|C5",
  "justificacion": "justificación clínica basada en Protocolo Manchester",
  "score_ansiedad": 0.0
}"""


# ------------------------------------------------------------------ #
# Helpers                                                              #
# ------------------------------------------------------------------ #

def _get_db() -> DatabaseService:
    return DatabaseService(DATABASE_URL)


def _get_minio() -> MinIOService:
    return MinIOService(MINIO_ENDPOINT, MINIO_ACCESS_KEY, MINIO_SECRET_KEY)


def _cargar_modelo():
    minio = _get_minio()
    raw   = minio.descargar_modelo()
    if raw is None:
        return None
    return joblib.load(BytesIO(raw))


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
    match = re.search(r"\{.*\}", sin_md, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    raise ValueError(f"Respuesta LLM sin JSON válido: {texto[:200]}")


# ------------------------------------------------------------------ #
# Schemas                                                              #
# ------------------------------------------------------------------ #

class PredictRequest(BaseModel):
    texto:         str
    mistral_key:   str | None = None   # opcional — usa MISTRAL_API_KEY del entorno si no se pasa
    system_prompt: str | None = None


class PredictResponse(BaseModel):
    guid:                  str
    etiqueta_llm:          str
    prediccion_ml:         str
    score_ansiedad:        float
    resumen:               str
    justificacion:         str
    entidades:             list[str]
    entidades_normalizadas: list[str]
    discrepancia:          bool


# ------------------------------------------------------------------ #
# Endpoints                                                            #
# ------------------------------------------------------------------ #

@router.post("/predict", response_model=PredictResponse)
def predict(req: PredictRequest):
    system_prompt = req.system_prompt or DEFAULT_SYSTEM_PROMPT
    mistral_key   = req.mistral_key or MISTRAL_API_KEY
    if not mistral_key:
        raise HTTPException(
            status_code=400,
            detail="Falta MISTRAL_API_KEY (ni en el request ni en la variable de entorno)",
        )

    guid = f"API{datetime.now().strftime('%Y%m%d%H%M%S%f')[:18]}"

    db    = _get_db()
    minio = _get_minio()

    # Guardar texto original en MinIO
    url_texto = minio.subir_texto(guid, req.texto)
    db.crear_prediccion(guid, url_texto)
    db.actualizar_prediccion(guid, inicio_extraccion_entidades=datetime.now())

    # Llamada LLM
    try:
        resp = httpx.post(
            MISTRAL_API_URL,
            headers={"Authorization": f"Bearer {mistral_key}", "Content-Type": "application/json"},
            json={
                "model":       "mistral-large-latest",
                "temperature": 0.1,
                "messages":    [
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": req.texto},
                ],
            },
            timeout=60.0,
        )
        resp.raise_for_status()
        datos = _extraer_json(resp.json()["choices"][0]["message"]["content"])
    except Exception as exc:
        db.actualizar_prediccion(guid, estado="ERROR")
        raise HTTPException(status_code=502, detail=f"Error LLM: {exc}")

    fin_llm = datetime.now()
    db.actualizar_prediccion(
        guid,
        fin_extraccion_entidades=fin_llm,
        inicio_normalizacion=fin_llm, fin_normalizacion=fin_llm,
        inicio_etiquetado=fin_llm, fin_etiquetado=fin_llm,
        inicio_score=fin_llm, fin_score=fin_llm,
    )

    # Predicción ML
    artefacto = _cargar_modelo()
    pred_ml   = "N/A"
    if artefacto:
        try:
            import numpy as np
            vec            = artefacto["vectorizador"]
            clf            = artefacto["modelo"]
            entidades_norm = datos.get("entidades_normalizadas", [])
            score          = float(datos.get("score_ansiedad", 0.0))
            texto_ents     = " ".join(entidades_norm)
            X_vec          = vec.transform([texto_ents]).toarray()
            X              = np.hstack([X_vec, [[score]]])
            pred_ml        = str(clf.predict(X)[0])
        except Exception:
            pass

    etiqueta_llm   = datos.get("triage_real", "")
    score_ans      = float(datos.get("score_ansiedad", 0.0))
    entidades_norm = datos.get("entidades_normalizadas", [])

    # Persistir JSON enriquecido en MinIO (igual que dag_llm_enrichment y Streamlit)
    enriquecido = {
        "guid":                   guid,
        "origen":                 "API",
        "categoria":              "API",
        "texto":                  req.texto,
        "resumen":                datos.get("resumen_es", ""),
        "entidades":              datos.get("entidades_extraidas", []),
        "entidades_normalizadas": entidades_norm,
        "etiqueta":               etiqueta_llm,
        "razonamiento":           datos.get("justificacion", ""),
        "score_ansiedad":         score_ans,
        "prediccion_entrenada":   pred_ml,
    }
    minio.subir_json(guid, enriquecido)

    db.actualizar_prediccion(
        guid,
        estado="PREDICCION_COMPLETADA",
        fin_solicitud=datetime.now(),
    )

    return PredictResponse(
        guid=guid,
        etiqueta_llm=etiqueta_llm,
        prediccion_ml=pred_ml,
        score_ansiedad=score_ans,
        resumen=datos.get("resumen_es", ""),
        justificacion=datos.get("justificacion", ""),
        entidades=datos.get("entidades_extraidas", []),
        entidades_normalizadas=entidades_norm,
        discrepancia=(etiqueta_llm != pred_ml and pred_ml != "N/A"),
    )


@router.get("/entrevista/{guid}")
def detalle_entrevista(guid: str):
    db    = _get_db()
    minio = _get_minio()

    row = db.obtener_entrevista(guid)
    if not row:
        raise HTTPException(status_code=404, detail="Entrevista no encontrada")

    for k, v in row.items():
        if hasattr(v, "isoformat"):
            row[k] = v.isoformat()

    row["enriquecido"] = minio.descargar_json(guid)
    return row
