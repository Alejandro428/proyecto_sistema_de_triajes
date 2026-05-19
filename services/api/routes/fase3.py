"""
Endpoints de predicción Manchester divididos por fase.
El frontend los llama secuencialmente y muestra progreso entre cada paso.

  POST /fase3/transcribir   audio → {guid, transcripcion, tiempo}
  POST /fase3/extraer       texto → {entidades, categoria, score, tiempo}
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
from pathlib import Path

import httpx
import numpy as np
from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from config import DATABASE_URL, MINIO_ENDPOINT, MINIO_ACCESS_KEY, MINIO_SECRET_KEY, MISTRAL_API_KEY
from services.db import DatabaseService
from services.minio_service import MinIOService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/fase3", tags=["Fase 3 — Predicción"])

MISTRAL_API_URL = "https://api.mistral.ai/v1/chat/completions"
MODELS_DIR      = Path("/app/models")

# Categorías de sistema corporal que entiende el modelo. Si el LLM devuelve
# otra cosa ("GENERAL", "card", etc.) caemos en "RES" para que el one-hot
# encoding del modelo no quede todo a cero.
CATEGORIAS_VALIDAS = {"CAR", "RES", "MSK", "GAS", "GEN", "DER"}


# ─────────────────────────────────────────────────────────────────────────────
# PROMPT
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """Eres un asistente clínico que EXTRAE features de una entrevista médica transcrita.
NO clasifiques el caso en niveles Manchester C1-C5: esa clasificación la realiza
un modelo de Machine Learning por separado. Sí debes describir clínicamente
el caso (resumen + justificación de gravedad) para que el médico lo lea.

## CATEGORÍAS DE SISTEMA CORPORAL
CAR (cardiovascular), RES (respiratorio), MSK (musculoesquelético),
GAS (gastrointestinal), GEN (general), DER (dermatológico).

## VOCABULARIO CLÍNICO CERRADO — 20 ETIQUETAS (usa EXACTAMENTE UNA)
Prioridad 1 (alarma vital):    Disnea, Dolor_Torácico, Síncope, Hemoptisis
Prioridad 2 (urgente):         Sibilancias, Palpitaciones, Dolor_Abdominal, Fiebre, Mareo
Prioridad 3 (común):           Tos, Náuseas_Vómitos, Cefalea, Diarrea, Edema, Odinofagia, Congestión_Respiratoria
Prioridad 4 (leve):            Fatiga, Dolor_Musculoesquelético, Anosmia, Traumatismo

Si hay varios síntomas, elige el de MÁS gravedad (prioridad más baja).

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
Devuelve ÚNICAMENTE un JSON válido, sin texto adicional. La lista
"entidades_normalizadas" debe contener UNA SOLA etiqueta del vocabulario cerrado.
Reemplaza el valor de score_ansiedad por tu estimación real (NO copies 0.0).
"resumen_es" debe ser 2-3 frases en español describiendo los síntomas principales.
"justificacion" debe explicar brevemente la gravedad clínica observada SIN
asignar nivel Manchester (eso lo hace el modelo ML).

Ejemplo:
{
  "resumen_es":            "Paciente con dolor torácico opresivo y disnea de aparición reciente. Refiere miedo intenso. Cuadro compatible con evento cardiovascular agudo.",
  "entidades_extraidas":   ["dolor en el pecho", "me cuesta respirar", "tengo miedo"],
  "entidades_normalizadas":["Dolor_Torácico"],
  "categoria":             "CAR",
  "score_ansiedad":        0.75,
  "justificacion":         "Dolor torácico opresivo asociado a disnea sugiere posible isquemia miocárdica."
}"""


# ─────────────────────────────────────────────────────────────────────────────
# DICCIONARIO CLÍNICO
# ─────────────────────────────────────────────────────────────────────────────

DICCIONARIO_PRIORIDAD = {
    "Disnea":                  1, "Dolor_Torácico":          1,
    "Síncope":                 1, "Hemoptisis":              1,
    "Sibilancias":             2, "Palpitaciones":           2,
    "Dolor_Abdominal":         2, "Fiebre":                  2, "Mareo": 2,
    "Tos":                     3, "Náuseas_Vómitos":         3, "Cefalea": 3,
    "Diarrea":                 3, "Edema":                   3, "Odinofagia": 3,
    "Congestión_Respiratoria": 3,
    "Fatiga":                  4, "Dolor_Musculoesquelético":4,
    "Anosmia":                 4, "Traumatismo":             4,
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
    # Síncope (1)
    "síncope":"Síncope","pérdida de conciencia":"Síncope","desmayo":"Síncope",
    "lipotimia":"Síncope","alteración conciencia":"Síncope",
    "confusión":"Síncope","desorientación":"Síncope",
    # Hemoptisis (1)
    "hemoptisis":"Hemoptisis","expectoración hemoptoica":"Hemoptisis",
    "esputo con sangre":"Hemoptisis","expectoración con sangre":"Hemoptisis",
    # Sibilancias (2)
    "sibilancias":"Sibilancias","pitos":"Sibilancias","tos productiva":"Sibilancias",
    # Palpitaciones (2)
    "palpitaciones":"Palpitaciones","taquicardia":"Palpitaciones","arritmia":"Palpitaciones",
    # Dolor abdominal (2)
    "dolor abdominal":"Dolor_Abdominal","dolor en el abdomen":"Dolor_Abdominal",
    "epigastralgia":"Dolor_Abdominal","cólico abdominal":"Dolor_Abdominal","dolor pélvico":"Dolor_Abdominal",
    # Fiebre (2)
    "fiebre":"Fiebre","fiebre/hipertermia":"Fiebre","hipertermia":"Fiebre",
    "fiebre alta":"Fiebre","febrícula":"Fiebre","escalofríos":"Fiebre","tiritona":"Fiebre",
    "sudoración nocturna":"Fiebre","diaforesis":"Fiebre","pérdida de peso":"Fiebre","amigdalitis":"Fiebre",
    # Mareo (2)
    "mareo":"Mareo","mareo/vértigo":"Mareo","vértigo":"Mareo","inestabilidad":"Mareo",
    # Tos (3)
    "tos":"Tos","tos seca":"Tos","tos crónica":"Tos","tos persistente":"Tos",
    # Náuseas/Vómitos (3)
    "náuseas":"Náuseas_Vómitos","vómitos":"Náuseas_Vómitos","náuseas/vómitos":"Náuseas_Vómitos",
    "arcadas":"Náuseas_Vómitos","regurgitación":"Náuseas_Vómitos",
    # Cefalea (3)
    "cefalea":"Cefalea","dolor de cabeza":"Cefalea","migraña":"Cefalea",
    # Diarrea (3)
    "diarrea":"Diarrea","deposiciones líquidas":"Diarrea","heces blandas":"Diarrea",
    # Edema (3)
    "edema":"Edema","edema/inflamación":"Edema","inflamación":"Edema","hinchazón":"Edema",
    # Odinofagia (3)
    "odinofagia":"Odinofagia","dolor de garganta":"Odinofagia",
    "disfagia":"Odinofagia","dificultad para tragar":"Odinofagia",
    # Congestión respiratoria (3)
    "rinorrea":"Congestión_Respiratoria","congestión nasal":"Congestión_Respiratoria",
    "congestión respiratoria":"Congestión_Respiratoria","secreción nasal":"Congestión_Respiratoria",
    "expectoración":"Congestión_Respiratoria","moqueo":"Congestión_Respiratoria",
    # Fatiga (4)
    "fatiga":"Fatiga","cansancio":"Fatiga","astenia":"Fatiga","debilidad":"Fatiga","letargo":"Fatiga",
    # Dolor musculoesquelético (4)
    "dolor musculoesquelético":"Dolor_Musculoesquelético","dolor muscular":"Dolor_Musculoesquelético",
    "dolor articular":"Dolor_Musculoesquelético","dolor cervical":"Dolor_Musculoesquelético",
    "dolor lumbar":"Dolor_Musculoesquelético","lumbago":"Dolor_Musculoesquelético",
    "dolor de espalda":"Dolor_Musculoesquelético","rigidez articular":"Dolor_Musculoesquelético",
    # Anosmia (4)
    "anosmia":"Anosmia","hiposmia":"Anosmia","pérdida de olfato":"Anosmia","ageusia":"Anosmia",
    # Traumatismo (4)
    "traumatismo":"Traumatismo","esguince":"Traumatismo","fractura":"Traumatismo",
    "contusión":"Traumatismo","luxación":"Traumatismo","herida":"Traumatismo",
    "erupción cutánea":"Traumatismo","rash":"Traumatismo","exantema":"Traumatismo","lesión cutánea":"Traumatismo",
}


def _entidad_principal(entidades: list) -> str:
    estandar = set()
    for e in entidades:
        original = (e or "").strip()
        # Si el LLM ya devolvió una etiqueta del vocabulario cerrado
        # (p.ej. "Dolor_Torácico"), usarla directamente sin pasar por MAPEO
        # que solo conoce variantes en lenguaje natural.
        if original in DICCIONARIO_PRIORIDAD:
            estandar.add(original)
            continue
        k = original.lower()
        if k in MAPEO:
            estandar.add(MAPEO[k])
        else:
            for key, val in MAPEO.items():
                if key in k and len(key) >= 5:
                    estandar.add(val)
                    break
    if not estandar:
        return "Sin_entidad"
    return min(estandar, key=lambda x: (DICCIONARIO_PRIORIDAD.get(x, 99), x))


# ─────────────────────────────────────────────────────────────────────────────
# CARGA PEREZOSA DE MODELOS (singletons)
# ─────────────────────────────────────────────────────────────────────────────

_whisper_model = None
_orange_model  = None
_orange_mtime  = 0.0   # mtime del .pkcls cargado, para hot-reload


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


def _cargar_orange():
    """Carga el modelo Orange más reciente. Si el .pkcls cambia en disco
    (reentrenado y sustituido en runtime), se recarga automáticamente."""
    global _orange_model, _orange_mtime
    if not MODELS_DIR.exists():
        return None
    pkcls = sorted(MODELS_DIR.glob("*.pkcls"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not pkcls:
        return None
    mtime_actual = pkcls[0].stat().st_mtime
    if _orange_model is None or mtime_actual > _orange_mtime:
        with open(pkcls[0], "rb") as f:
            _orange_model = pickle.load(f)
        _orange_mtime = mtime_actual
        logger.info("modelo Orange cargado: %s (mtime=%d)", pkcls[0].name, int(mtime_actual))
    return _orange_model


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
    # Campos opcionales que el frontend reenvía para almacenarlos en el JSON
    # enriquecido (sirven para el detalle del historial). No participan en la
    # predicción ML.
    texto:                  str        = ""
    resumen:                str        = ""
    entidades:              list[str]  = []
    razonamiento:           str        = ""


class PredecirResponse(BaseModel):
    guid:              str
    prediccion_ml:     str
    entidad_principal: str
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

    # Subir audio a MinIO + fila inicial en BD
    try:
        minio.subir_audio(guid, audio, ext)
    except Exception:
        logger.exception("[transcribir] no se pudo subir audio %s", guid)

    db.crear_prediccion(guid, f"minio://audios/{guid}.{ext}")

    # Whisper
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
                "model": "mistral-medium-latest",
                "temperature": 0.1,
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
    db.actualizar_prediccion(
        req.guid,
        fin_extraccion_entidades=fin_llm,
        inicio_score=fin_llm, fin_score=fin_llm,
    )

    categoria_raw = (datos.get("categoria", "") or "").strip().upper()
    categoria     = categoria_raw if categoria_raw in CATEGORIAS_VALIDAS else "RES"

    return ExtraerResponse(
        guid=req.guid,
        entidades=datos.get("entidades_extraidas", []) or [],
        entidades_normalizadas=datos.get("entidades_normalizadas", []) or [],
        categoria=categoria,
        score_ansiedad=float(datos.get("score_ansiedad", 0.0) or 0.0),
        resumen=datos.get("resumen_es", "") or "",
        razonamiento=datos.get("justificacion", "") or "",
        tiempo=tiempo,
    )


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINT 3 — ML (Orange)
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/predecir", response_model=PredecirResponse)
def predecir(req: PredecirRequest):
    """Predice C1-C5 con el modelo Orange entrenado."""
    db = _get_db()
    t0 = time.time()
    db.actualizar_prediccion(req.guid, inicio_entrenamiento=datetime.now())

    modelo  = _cargar_orange()
    entidad = _entidad_principal(req.entidades_normalizadas)

    if modelo is None:
        pred = "N/A"
    else:
        try:
            from Orange.data import Table
            domain = modelo.domain
            fila   = []
            for attr in domain.attributes:
                name = attr.name
                if "=" in name:
                    var, val = name.split("=", 1)
                    if var == "entidad_principal":
                        fila.append(1.0 if val == entidad else 0.0)
                    elif var == "categoria":
                        fila.append(1.0 if val == req.categoria else 0.0)
                    else:
                        fila.append(0.0)
                elif name == "score_ansiedad":
                    fila.append(float(req.score_ansiedad))
                else:
                    fila.append(0.0)
            Y = np.array([[np.nan]])
            M = np.array([[""] * len(domain.metas)], dtype=object) if domain.metas else None
            tabla = Table.from_numpy(domain, np.array([fila]), Y, M)
            pred  = str(domain.class_var.values[int(modelo(tabla)[0])])
        except Exception:
            logger.exception("[predecir] fallo al ejecutar el modelo Orange para %s", req.guid)
            pred = "N/A"

    tiempo   = round(time.time() - t0, 3)
    fin_pred = datetime.now()

    # Guardar JSON enriquecido en MinIO (mismo formato que dag_llm_enrichment
    # para que el detalle del historial muestre los mismos campos)
    minio = _get_minio()
    try:
        minio.subir_json(req.guid, {
            "guid":                   req.guid,
            "origen":                 "API",
            "categoria":              req.categoria,
            "texto":                  req.texto,
            "resumen":                req.resumen,
            "entidades":              req.entidades,
            "entidades_normalizadas": req.entidades_normalizadas,
            "entidad_principal":      entidad,
            "razonamiento":           req.razonamiento,
            "score_ansiedad":         req.score_ansiedad,
            "prediccion_entrenada":   pred,
        })
    except Exception:
        logger.exception("[predecir] no se pudo subir JSON enriquecido para %s", req.guid)

    db.actualizar_prediccion(
        req.guid,
        fin_entrenamiento=fin_pred,
        fin_solicitud=datetime.now(),
        estado="PREDICCION_COMPLETADA",
    )

    return PredecirResponse(
        guid=req.guid, prediccion_ml=pred, entidad_principal=entidad, tiempo=tiempo,
    )
