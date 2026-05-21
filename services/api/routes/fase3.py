"""
Endpoints de predicción Manchester divididos por fase.
El frontend los llama secuencialmente y muestra progreso entre cada paso.

  POST /fase3/transcribir   audio → {guid, transcripcion, tiempo}
  POST /fase3/extraer       texto → {entidades, categoria, score, resumen, razonamiento, tiempo}
  POST /fase3/predecir      features → {prediccion, tiempo}
"""

import json
import logging
import os
import pickle
import re
import tempfile
import time
import uuid
from datetime import datetime
from io import BytesIO
from pathlib import Path

logger = logging.getLogger(__name__)

import httpx
import numpy as np
from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from config import DATABASE_URL, MINIO_ENDPOINT, MINIO_ACCESS_KEY, MINIO_SECRET_KEY, MISTRAL_API_KEY
from services.db import DatabaseService
from services.minio_service import MinIOService

router = APIRouter(prefix="/fase3", tags=["Fase 3 — Predicción"])

MISTRAL_API_URL = "https://api.mistral.ai/v1/chat/completions"
MISTRAL_MODEL   = "mistral-medium-latest"
MODELS_DIR      = Path("/app/models")


# ─────────────────────────────────────────────────────────────────────────────
# PROMPT
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """Eres un asistente clínico que EXTRAE features de una entrevista médica transcrita.
NO clasifiques el caso en niveles Manchester. La clasificación C1-C5 la realiza un modelo de Machine Learning.

## CATEGORÍAS DE SISTEMA CORPORAL
CAR (cardiovascular), RES (respiratorio), MSK (musculoesquelético),
GAS (gastrointestinal), GEN (general), DER (dermatológico).

## VOCABULARIO CLÍNICO CERRADO — 10 ETIQUETAS
Prioridad 1 (alarma vital):    Disnea, Dolor_Torácico
Prioridad 2 (urgente):         Fiebre, Dolor_Abdominal, Palpitaciones
Prioridad 3 (común):           Cefalea, Náuseas_Vómitos
Prioridad 4 (leve):            Tos
Prioridad 5 (no urgente):      Fatiga, Dolor_Musculoesquelético

REGLAS:
- Devuelve entre 1 y 5 etiquetas DEL VOCABULARIO en `entidades_normalizadas`.
- Si hay más de 5 síntomas, prioriza los más graves (prioridad 1 > 2 > 3 > 4 > 5).
- Si no se detecta ningún síntoma claro, devuelve una lista vacía [].
- Usa SOLO etiquetas del vocabulario; ignora síntomas que no encajen.

## SCORE_ANSIEDAD — IMPORTANTE
Estima el nivel de ansiedad/angustia emocional del paciente entre 0.0 y 1.0
basándote en frases, tono y miedo expresado. NO devuelvas 0.0 por defecto.

Guía de estimación:
- 0.0–0.2  Paciente tranquilo, narra los síntomas sin alarma
            ("vengo a una revisión", "me cuesta un poco respirar")
- 0.3–0.5  Preocupación moderada, refiere ligera inquietud
            ("estoy preocupado", "no sé qué me pasa", "me asusta un poco")
- 0.6–0.8  Ansiedad clara, frases de miedo o urgencia
            ("tengo miedo de morirme", "me siento muy mal", "es horrible")
- 0.9–1.0  Pánico o crisis ansiosa intensa
            ("me ahogo, me muero", "no puedo respirar, ayuda", "auxilio")

## FORMATO DE SALIDA
Devuelve ÚNICAMENTE un JSON válido, sin texto adicional.

Ejemplo (paciente con varios síntomas):
{
  "resumen_es":             "Paciente refiere dolor en el pecho opresivo con dificultad para respirar y sudoración fría.",
  "entidades_extraidas":    ["dolor en el pecho", "me cuesta respirar", "sudoración fría"],
  "entidades_normalizadas": ["Dolor_Torácico", "Disnea"],
  "categoria":              "CAR",
  "justificacion":          "El síntoma principal es dolor torácico con posible irradiación y disnea asociada, compatible con evento cardiovascular.",
  "score_ansiedad":         0.75
}"""


# ─────────────────────────────────────────────────────────────────────────────
# DICCIONARIO CLÍNICO
# ─────────────────────────────────────────────────────────────────────────────

DICCIONARIO_PRIORIDAD = {
    "Disnea":                   1,
    "Dolor_Torácico":           1,
    "Fiebre":                   2,
    "Dolor_Abdominal":          2,
    "Palpitaciones":            2,
    "Cefalea":                  3,
    "Náuseas_Vómitos":          3,
    "Tos":                      4,
    "Fatiga":                   5,
    "Dolor_Musculoesquelético": 5,
}

MAPEO = {
    # Disnea (1)
    "disnea":"Disnea","disnea de esfuerzo":"Disnea","disnea de reposo":"Disnea",
    "dificultad respiratoria":"Disnea","dificultad para respirar":"Disnea",
    "ortopnea":"Disnea","broncoespasmo":"Disnea","estridor":"Disnea","cianosis":"Disnea",
    # Dolor torácico (1)
    "dolor torácico":"Dolor_Torácico","dolor torácico agudo":"Dolor_Torácico",
    "dolor torácico opresivo":"Dolor_Torácico","dolor en el pecho":"Dolor_Torácico",
    "presión torácica":"Dolor_Torácico","opresión torácica":"Dolor_Torácico",
    "dolor irradiado":"Dolor_Torácico","dolor precordial":"Dolor_Torácico",
    # Fiebre (2)
    "fiebre":"Fiebre","fiebre/hipertermia":"Fiebre","hipertermia":"Fiebre",
    "fiebre alta":"Fiebre","febrícula":"Fiebre","escalofríos":"Fiebre","tiritona":"Fiebre",
    "sudoración nocturna":"Fiebre","diaforesis":"Fiebre","pérdida de peso":"Fiebre","amigdalitis":"Fiebre",
    # Dolor abdominal (2)
    "dolor abdominal":"Dolor_Abdominal","dolor en el abdomen":"Dolor_Abdominal",
    "epigastralgia":"Dolor_Abdominal","cólico abdominal":"Dolor_Abdominal","dolor pélvico":"Dolor_Abdominal",
    # Palpitaciones (2)
    "palpitaciones":"Palpitaciones","taquicardia":"Palpitaciones","arritmia":"Palpitaciones",
    # Cefalea (3)
    "cefalea":"Cefalea","dolor de cabeza":"Cefalea","migraña":"Cefalea",
    # Náuseas/Vómitos (3)
    "náuseas":"Náuseas_Vómitos","vómitos":"Náuseas_Vómitos","náuseas/vómitos":"Náuseas_Vómitos",
    "arcadas":"Náuseas_Vómitos","regurgitación":"Náuseas_Vómitos",
    # Tos (4)
    "tos":"Tos","tos seca":"Tos","tos crónica":"Tos","tos persistente":"Tos","tos productiva":"Tos",
    # Fatiga (5)
    "fatiga":"Fatiga","cansancio":"Fatiga","astenia":"Fatiga","debilidad":"Fatiga","letargo":"Fatiga",
    # Dolor musculoesquelético (5)
    "dolor musculoesquelético":"Dolor_Musculoesquelético","dolor muscular":"Dolor_Musculoesquelético",
    "dolor articular":"Dolor_Musculoesquelético","dolor cervical":"Dolor_Musculoesquelético",
    "dolor lumbar":"Dolor_Musculoesquelético","lumbago":"Dolor_Musculoesquelético",
    "dolor de espalda":"Dolor_Musculoesquelético","rigidez articular":"Dolor_Musculoesquelético",
}


def _entidades_estandar(entidades: list) -> list:
    """Devuelve TODAS las entidades estándar detectadas, ordenadas por gravedad."""
    estandar = set()
    for e in entidades:
        if e in DICCIONARIO_PRIORIDAD:
            estandar.add(e)
            continue
        k = (e or "").strip().lower()
        if k in MAPEO:
            estandar.add(MAPEO[k])
        else:
            for key, val in MAPEO.items():
                if key in k and len(key) >= 5:
                    estandar.add(val)
                    break
    return sorted(estandar, key=lambda x: (DICCIONARIO_PRIORIDAD.get(x, 99), x))


def _entidad_principal(entidades: list) -> str:
    """Devuelve la entidad de mayor prioridad clínica (la primera tras ordenar)."""
    ents = _entidades_estandar(entidades)
    return ents[0] if ents else "Sin_entidad"


# ─────────────────────────────────────────────────────────────────────────────
# CARGA PEREZOSA DE MODELOS (singletons)
# ─────────────────────────────────────────────────────────────────────────────

_whisper_model  = None
_orange_models: dict = {}  # {filename: model}


def _get_db() -> DatabaseService:
    return DatabaseService(DATABASE_URL)


def _get_minio() -> MinIOService:
    return MinIOService(MINIO_ENDPOINT, MINIO_ACCESS_KEY, MINIO_SECRET_KEY)


def _cargar_whisper():
    global _whisper_model
    if _whisper_model is None:
        import whisper
        _whisper_model = whisper.load_model("base")
    return _whisper_model


def _listar_pkcls() -> list[Path]:
    if not MODELS_DIR.exists():
        return []
    return sorted(MODELS_DIR.glob("*.pkcls"), key=lambda p: p.stat().st_mtime, reverse=True)


def _cargar_orange(nombre: str | None = None):
    """Carga y cachea un modelo por nombre de archivo. Sin nombre → el más reciente."""
    global _orange_models
    archivos = _listar_pkcls()
    if not archivos:
        return None, None
    target = MODELS_DIR / nombre if nombre else archivos[0]
    if not target.exists():
        return None, nombre
    key = target.name
    if key not in _orange_models:
        with open(target, "rb") as f:
            _orange_models[key] = pickle.load(f)
    return _orange_models[key], key


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


# ─────────────────────────────────────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────────────────────────────────────

class TranscribirResponse(BaseModel):
    guid:          str
    transcripcion: str
    tiempo:        float


class ExtraerRequest(BaseModel):
    guid:  str
    texto: str


class ExtraerResponse(BaseModel):
    guid:                    str
    entidades:               list[str]
    entidades_normalizadas:  list[str]
    categoria:               str
    score_ansiedad:          float
    resumen:                 str
    razonamiento:            str
    tiempo:                  float


class PredecirRequest(BaseModel):
    guid:                   str
    entidades_normalizadas: list[str]
    categoria:              str
    score_ansiedad:         float
    modelo:                 str | None = None  # None → el más reciente
    texto:                  str = ""
    resumen:                str = ""
    entidades:              list[str] = []
    razonamiento:           str = ""


class PredecirResponse(BaseModel):
    guid:              str
    prediccion_ml:     str
    entidad_principal: str
    modelo_usado:      str
    tiempo:            float


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINT 1 — Whisper
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/transcribir", response_model=TranscribirResponse)
async def transcribir(file: UploadFile = File(...)):
    """Recibe audio, lo transcribe con Whisper. Crea la fila en la BD."""
    db    = _get_db()
    minio = _get_minio()

    guid  = f"PAC{datetime.now().strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex[:4].upper()}"
    ext   = (file.filename or "audio.mp3").rsplit(".", 1)[-1].lower()
    audio = await file.read()
    if not audio:
        raise HTTPException(status_code=400, detail="Audio vacío")

    try:
        minio.subir_audio(guid, audio, ext)
    except Exception as exc:
        logger.warning("[transcribir] no se pudo subir audio: %s", exc)

    db.crear_prediccion(guid, f"minio://audios/{guid}.{ext}")

    t0 = time.time()
    db.actualizar_prediccion(guid, inicio_preprocesamiento=datetime.now())
    try:
        with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as tmp:
            tmp.write(audio)
            path = tmp.name
        try:
            modelo = _cargar_whisper()
            transcripcion = modelo.transcribe(path, language="es")["text"].strip()
        finally:
            try: os.unlink(path)
            except Exception: pass
    except Exception as exc:
        db.actualizar_prediccion(guid, estado="ERROR", fin_solicitud=datetime.now())
        raise HTTPException(status_code=500, detail=f"Whisper falló: {exc}")

    tiempo = round(time.time() - t0, 3)
    db.actualizar_prediccion(guid, fin_preprocesamiento=datetime.now())
    minio.subir_texto(guid, transcripcion)

    return TranscribirResponse(guid=guid, transcripcion=transcripcion, tiempo=tiempo)


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINT 2 — Mistral
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/extraer", response_model=ExtraerResponse)
def extraer(req: ExtraerRequest):
    """Recibe texto, extrae entidades con Mistral."""
    if not MISTRAL_API_KEY:
        raise HTTPException(status_code=500, detail="MISTRAL_API_KEY no configurada")

    db = _get_db()
    t0 = time.time()
    db.actualizar_prediccion(req.guid, inicio_extraccion_entidades=datetime.now())

    try:
        resp = httpx.post(
            MISTRAL_API_URL,
            headers={"Authorization": f"Bearer {MISTRAL_API_KEY}", "Content-Type": "application/json"},
            json={
                "model":           MISTRAL_MODEL,
                "temperature":     0.1,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user",   "content": req.texto},
                ],
            },
            timeout=60.0,
        )
        resp.raise_for_status()
        datos = _extraer_json(resp.json()["choices"][0]["message"]["content"])
    except Exception as exc:
        db.actualizar_prediccion(req.guid, estado="ERROR", fin_solicitud=datetime.now())
        raise HTTPException(status_code=502, detail=f"Mistral falló: {exc}")

    tiempo  = round(time.time() - t0, 3)
    fin_llm = datetime.now()
    db.actualizar_prediccion(req.guid, fin_extraccion_entidades=fin_llm)

    return ExtraerResponse(
        guid=req.guid,
        entidades=datos.get("entidades_extraidas", []) or [],
        entidades_normalizadas=datos.get("entidades_normalizadas", []) or [],
        categoria=(datos.get("categoria", "") or "RES").upper(),
        score_ansiedad=float(datos.get("score_ansiedad", 0.0) or 0.0),
        resumen=datos.get("resumen_es", "") or "",
        razonamiento=datos.get("justificacion", "") or "",
        tiempo=tiempo,
    )


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINT 3a — Listar modelos disponibles
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/modelos")
def listar_modelos():
    """Lista los modelos .pkcls disponibles en /app/models, ordenados por fecha."""
    archivos = _listar_pkcls()
    nombres  = [p.name for p in archivos]
    return {"modelos": nombres, "activo": nombres[0] if nombres else None}


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINT 3b — ML (Orange)
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/predecir", response_model=PredecirResponse)
def predecir(req: PredecirRequest):
    """Predice C1-C5 con el modelo Orange entrenado."""
    db = _get_db()
    t0 = time.time()
    db.actualizar_prediccion(req.guid, inicio_entrenamiento=datetime.now())

    modelo, modelo_key = _cargar_orange(req.modelo)
    entidades_set      = set(_entidades_estandar(req.entidades_normalizadas))
    entidad_ppal       = sorted(entidades_set, key=lambda x: (DICCIONARIO_PRIORIDAD.get(x, 99), x))[0] if entidades_set else "Sin_entidad"

    if modelo is None:
        pred = "N/A"
    else:
        try:
            from Orange.data import Table
            domain     = modelo.domain
            fila       = []
            n_sintomas = len(entidades_set)
            for attr in domain.attributes:
                name = attr.name
                if name in DICCIONARIO_PRIORIDAD:
                    fila.append(1.0 if name in entidades_set else 0.0)
                elif "=" in name:
                    var, val = name.split("=", 1)
                    if var == "categoria":
                        fila.append(1.0 if val == req.categoria else 0.0)
                    else:
                        fila.append(0.0)
                elif name == "score_ansiedad":
                    fila.append(float(req.score_ansiedad))
                elif name == "n_sintomas":
                    fila.append(float(n_sintomas))
                else:
                    fila.append(0.0)
            Y     = np.array([[np.nan]])
            M     = np.array([[""] * len(domain.metas)], dtype=object) if domain.metas else None
            tabla = Table.from_numpy(domain, np.array([fila]), Y, M)
            pred  = str(domain.class_var.values[int(modelo(tabla)[0])])
        except Exception as exc:
            logger.error("[predecir] error Orange: %s", exc)
            pred = "N/A"

    tiempo   = round(time.time() - t0, 3)
    fin_pred = datetime.now()

    minio = _get_minio()
    try:
        minio.subir_json(req.guid, {
            "guid":                   req.guid,
            "origen":                 "API",
            "texto":                  req.texto,
            "resumen":                req.resumen,
            "razonamiento":           req.razonamiento,
            "categoria":              req.categoria,
            "entidades":              req.entidades,
            "entidades_normalizadas": sorted(entidades_set, key=lambda x: (DICCIONARIO_PRIORIDAD.get(x, 99), x)),
            "entidad_principal":      entidad_ppal,
            "score_ansiedad":         req.score_ansiedad,
            "prediccion_entrenada":   pred,
            "modelo_usado":           modelo_key or "N/A",
        })
    except Exception as exc:
        logger.warning("[predecir] no se pudo subir JSON: %s", exc)

    db.actualizar_prediccion(
        req.guid,
        fin_entrenamiento=fin_pred,
        fin_solicitud=datetime.now(),
        estado="PREDICCION_COMPLETADA",
    )

    return PredecirResponse(
        guid=req.guid, prediccion_ml=pred, entidad_principal=entidad_ppal,
        modelo_usado=modelo_key or "N/A", tiempo=tiempo,
    )
