"""
TriageIA — Frontend de predicción
Audio → Whisper → Mistral → RandomForest → nivel Manchester
"""
import json
import os
import re
import tempfile
import time
from datetime import datetime
from io import BytesIO

import httpx
import joblib
import pandas as pd
import psycopg2
import streamlit as st
import whisper

# ── Configuración ───────────────────────────────────────────────────────────────
MINIO_ENDPOINT  = os.getenv("MINIO_ENDPOINT",  "localhost:9000")
MINIO_ACCESS    = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET    = os.getenv("MINIO_SECRET_KEY", "minioadmin")
DATABASE_URL    = os.getenv("DATABASE_URL",     "")
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY",  "")

BUCKET_AUDIOS       = "audios"
BUCKET_TEXTOS       = "textos"
BUCKET_ENRIQUECIDOS = "enriquecidos"
BUCKET_MODELOS      = "modelos"

MANCHESTER = {
    "C1": {"color": "#D32F2F", "bg": "#FFEBEE", "label": "EMERGENCIA",    "tiempo": "0 min",   "desc": "Riesgo vital inmediato"},
    "C2": {"color": "#E65100", "bg": "#FFF3E0", "label": "MUY URGENTE",   "tiempo": "10 min",  "desc": "Atención en máx. 10 minutos"},
    "C3": {"color": "#F9A825", "bg": "#FFFDE7", "label": "URGENTE",       "tiempo": "60 min",  "desc": "Atención en máx. 1 hora"},
    "C4": {"color": "#2E7D32", "bg": "#E8F5E9", "label": "MENOS URGENTE", "tiempo": "120 min", "desc": "Atención en máx. 2 horas"},
    "C5": {"color": "#1565C0", "bg": "#E3F2FD", "label": "NO URGENTE",    "tiempo": "240 min", "desc": "Atención en máx. 4 horas"},
}

SYSTEM_PROMPT = """Eres un médico experto en urgencias hospitalarias especializado en el Protocolo de Triaje Manchester.
Tu tarea es analizar transcripciones de entrevistas clínicas entre médico y paciente, y extraer información estructurada para un sistema de triaje automatizado.

## PROTOCOLO MANCHESTER
- C1 (Rojo, 0 min): Emergencia vital inmediata. Parada cardiorrespiratoria, síncope, obstrucción vía aérea.
- C2 (Naranja, 10 min): Muy urgente. Dolor torácico opresivo, disnea aguda, crisis asmática severa, posible infarto.
- C3 (Amarillo, 60 min): Urgente. Fiebre alta, sibilancias moderadas, dolor moderado, vómitos persistentes.
- C4 (Verde, 120 min): Menos urgente. Edema sin signos de alarma, dolor leve, síntomas leves controlados.
- C5 (Azul, 240 min): No urgente. Consulta rutinaria, síntomas mínimos de larga evolución.

## DICCIONARIO DE NORMALIZACIÓN OBLIGATORIO
Mapea siempre el lenguaje coloquial a estos términos clínicos estándar:

- "can't breathe", "short of breath", "no puedo respirar", "falta de aire" → Disnea → C1 (súbita) / C2 (aguda)
- "wheezing", "whistling", "pitos", "silbidos al respirar" → Sibilancias → C2 / C3
- "chest pressure", "like an elephant on my chest", "chest tightness" → Dolor Torácico Opresivo → C2
- "fever", "high temperature", "burning up", "fiebre", "calentura" → Fiebre/Hipertermia → C3
- "passed out", "fainted", "lost consciousness", "se desplomó" → Síncope → C1
- "swollen", "swelling", "hinchado", "inflamado" → Edema/Inflamación → C4
- "sharp chest pain", "stabbing chest pain" → Dolor Torácico Agudo → C2 / C3
- "cough", "tos", "coughing" → Tos → C3 / C4 según severidad
- "nausea", "vomiting", "náuseas", "vómitos" → Náuseas/Vómitos → C3 / C4
- "muscle pain", "joint pain", "back pain", "dolor muscular", "lumbago" → Dolor Musculoesquelético → C4
- "dizziness", "mareo", "lightheaded" → Mareo/Vértigo → C3 / C4
- "fatigue", "cansancio", "tiredness" → Fatiga → C4 / C5

## REGLA CRÍTICA
La clínica siempre prevalece sobre el estado emocional.
Un paciente con disnea real y ansiedad extrema es C2, NO C3.
El score_ansiedad es solo informativo y NUNCA determina el nivel de triaje.

## FORMATO DE SALIDA
Devuelve ÚNICAMENTE un JSON válido, sin texto adicional antes ni después:
{
  "resumen_es": "resumen breve en español de los síntomas principales (2-3 frases)",
  "entidades_extraidas": ["síntoma tal como aparece en el texto original"],
  "entidades_normalizadas": ["término clínico estándar en español"],
  "triage_real": "C1|C2|C3|C4|C5",
  "justificacion": "justificación clínica breve basada en el Protocolo Manchester",
  "score_ansiedad": 0.0
}

## EJEMPLOS FEW-SHOT

### Ejemplo 1 — C2 con síntoma cardíaco
Transcripción: "Doctor, I've been having really bad chest pain since this morning. It feels like someone is sitting on my chest. I also feel short of breath and my left arm feels numb and heavy."
Respuesta:
{
  "resumen_es": "Paciente con dolor torácico opresivo de inicio matutino, disnea asociada y entumecimiento en brazo izquierdo. Cuadro compatible con síndrome coronario agudo.",
  "entidades_extraidas": ["chest pain", "like someone sitting on my chest", "short of breath", "left arm numb and heavy"],
  "entidades_normalizadas": ["Dolor Torácico Opresivo", "Disnea", "Dolor Irradiado"],
  "triage_real": "C2",
  "justificacion": "Dolor torácico opresivo con irradiación a brazo izquierdo y disnea. Clínica compatible con síndrome coronario agudo. Manchester C2: Muy Urgente.",
  "score_ansiedad": 0.65
}

### Ejemplo 2 — C3 con síntoma respiratorio leve
Transcripción: "I've had a cough for about a week now, and I've been running a fever of 38.8 degrees. I feel tired and my throat is sore. No shortness of breath."
Respuesta:
{
  "resumen_es": "Paciente con tos de una semana de evolución, fiebre de 38.8°C, fatiga y odinofagia. Sin disnea asociada.",
  "entidades_extraidas": ["cough", "fever 38.8", "tired", "sore throat"],
  "entidades_normalizadas": ["Tos", "Fiebre/Hipertermia", "Fatiga", "Odinofagia"],
  "triage_real": "C3",
  "justificacion": "Síndrome febril con tos y odinofagia compatible con infección respiratoria alta. Sin signos de gravedad. Manchester C3: Urgente.",
  "score_ansiedad": 0.2
}

### Ejemplo 3 — C2 con ansiedad alta (la clínica prevalece)
Transcripción: "I can't breathe at all, I'm so scared, I think I'm dying, there's wheezing when I breathe and it started suddenly 30 minutes ago. I'm really panicking."
Respuesta:
{
  "resumen_es": "Paciente con disnea brusca y sibilancias de 30 minutos de evolución con inicio súbito. Alta ansiedad secundaria a la dificultad respiratoria.",
  "entidades_extraidas": ["can't breathe", "wheezing", "started suddenly"],
  "entidades_normalizadas": ["Disnea", "Sibilancias"],
  "triage_real": "C2",
  "justificacion": "Disnea con sibilancias de inicio brusco compatible con crisis asmática severa. La ansiedad es secundaria a la clínica y no modifica el nivel. Manchester C2: Muy Urgente.",
  "score_ansiedad": 0.95
}

### Ejemplo 4 — C4 musculoesquelético
Transcripción: "I twisted my ankle playing football yesterday. It's swollen and it hurts when I walk but I can still put some weight on it. No other symptoms."
Respuesta:
{
  "resumen_es": "Paciente con esguince de tobillo tras traumatismo deportivo. Edema local con capacidad de carga parcial conservada.",
  "entidades_extraidas": ["twisted ankle", "swollen", "pain when walking"],
  "entidades_normalizadas": ["Traumatismo Tobillo", "Edema/Inflamación", "Dolor Musculoesquelético"],
  "triage_real": "C4",
  "justificacion": "Esguince de tobillo con edema moderado. Capacidad de carga parcial conservada, sin signos de fractura evidente. Manchester C4: Menos Urgente.",
  "score_ansiedad": 0.1
}
"""

# ── Clientes (cacheados para toda la sesión) ────────────────────────────────────

@st.cache_resource
def get_minio():
    from minio import Minio
    client = Minio(MINIO_ENDPOINT, access_key=MINIO_ACCESS, secret_key=MINIO_SECRET, secure=False)
    for bucket in [BUCKET_AUDIOS, BUCKET_TEXTOS, BUCKET_ENRIQUECIDOS, BUCKET_MODELOS]:
        if not client.bucket_exists(bucket):
            client.make_bucket(bucket)
    return client


@st.cache_resource
def get_whisper():
    return whisper.load_model("base")


@st.cache_resource
def get_modelo():
    try:
        client = get_minio()
        data = client.get_object(BUCKET_MODELOS, "modelo_triageia.pkl").read()
        return joblib.load(BytesIO(data))
    except Exception:
        return None


# ── Base de datos ───────────────────────────────────────────────────────────────

def db_crear(guid: str, url_audio: str) -> None:
    with psycopg2.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO entrevista
                    (guid_entrevista, url_texto_original,
                     inicio_solicitud, motor_workflow, estado)
                VALUES (%s, %s, %s, 'Streamlit', 'PREDICIENDO')
                ON CONFLICT (guid_entrevista) DO NOTHING
            """, (guid, url_audio, datetime.now()))


def db_update(guid: str, **kwargs) -> None:
    if not kwargs:
        return
    fields = ", ".join(f"{k} = %s" for k in kwargs)
    vals   = list(kwargs.values()) + [guid]
    with psycopg2.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE entrevista SET {fields} WHERE guid_entrevista = %s",
                vals,
            )


# ── Mistral ─────────────────────────────────────────────────────────────────────

def _parse_json(texto: str) -> dict:
    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", texto, re.DOTALL)
    if m:
        return json.loads(m.group())
    raise ValueError(f"Respuesta Mistral sin JSON válido: {texto[:200]}")


def llamar_mistral(texto: str) -> dict:
    resp = httpx.post(
        "https://api.mistral.ai/v1/chat/completions",
        headers={"Authorization": f"Bearer {MISTRAL_API_KEY}", "Content-Type": "application/json"},
        json={
            "model":       "mistral-large-latest",
            "messages":    [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": texto},
            ],
            "temperature": 0.1,
        },
        timeout=60.0,
    )
    resp.raise_for_status()
    return _parse_json(resp.json()["choices"][0]["message"]["content"])


# ── Predicción ML ────────────────────────────────────────────────────────────────

def predecir(entidades: list, score_ansiedad: float) -> str:
    artefacto = get_modelo()
    if artefacto is None:
        return "N/A"
    clf = artefacto["modelo"]
    mlb = artefacto["binarizador"]
    X = pd.DataFrame(mlb.transform([entidades]), columns=mlb.classes_)
    X["score_ansiedad"] = score_ansiedad
    return clf.predict(X)[0]


# ── Página ──────────────────────────────────────────────────────────────────────

st.set_page_config(page_title="TriageIA", page_icon="🏥", layout="centered")

st.title("🏥 TriageIA")
st.caption("Sistema de Triaje Manchester — Análisis automático de entrevistas clínicas")
st.divider()

uploaded = st.file_uploader(
    "Sube el audio de la entrevista clínica",
    type=["mp3", "wav", "m4a", "ogg", "webm"],
)

if uploaded:
    st.audio(uploaded)

    if st.button("Analizar entrevista", type="primary", use_container_width=True):
        guid        = f"PAC{datetime.now().strftime('%Y%m%d%H%M%S')}"
        minio       = get_minio()
        ext         = uploaded.name.rsplit(".", 1)[-1].lower()
        audio_bytes = uploaded.getvalue()
        tiempos     = {}

        # Paso 1 — Guardar audio en MinIO
        with st.status("Guardando audio en MinIO...", expanded=False) as s:
            nombre_audio = f"{guid}.{ext}"
            minio.put_object(
                BUCKET_AUDIOS, nombre_audio,
                BytesIO(audio_bytes), len(audio_bytes),
                content_type=f"audio/{ext}",
            )
            url_audio = f"minio://{BUCKET_AUDIOS}/{nombre_audio}"
            db_crear(guid, url_audio)
            s.update(label="✓ Audio guardado", state="complete")

        # Paso 2 — Transcripción Whisper
        with st.status("Transcribiendo audio con Whisper...", expanded=True) as s:
            t0 = time.time()
            db_update(guid, inicio_preprocesamiento=datetime.now())

            wmodel = get_whisper()
            with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as tmp:
                tmp.write(audio_bytes)
                tmp_path = tmp.name
            try:
                transcripcion = wmodel.transcribe(tmp_path)["text"].strip()
            finally:
                os.unlink(tmp_path)

            texto_bytes = transcripcion.encode("utf-8")
            minio.put_object(BUCKET_TEXTOS, f"{guid}.txt", BytesIO(texto_bytes), len(texto_bytes))

            tiempos["Transcripción (Whisper)"] = time.time() - t0
            db_update(guid, fin_preprocesamiento=datetime.now())
            s.update(label=f"✓ Transcripción completada ({tiempos['Transcripción (Whisper)']:.1f}s)", state="complete")

        with st.expander("Ver transcripción completa"):
            st.write(transcripcion)

        # Paso 3 — Análisis Mistral
        with st.status("Analizando con Mistral IA...", expanded=True) as s:
            t0 = time.time()
            db_update(guid, inicio_extraccion_entidades=datetime.now())

            datos     = llamar_mistral(transcripcion)
            fin_llm   = datetime.now()

            tiempos["Análisis IA (Mistral)"] = time.time() - t0
            db_update(guid,
                fin_extraccion_entidades=fin_llm,
                inicio_normalizacion=fin_llm, fin_normalizacion=fin_llm,
                inicio_etiquetado=fin_llm,    fin_etiquetado=fin_llm,
                inicio_score=fin_llm,         fin_score=fin_llm,
            )
            s.update(label=f"✓ Análisis IA completado ({tiempos['Análisis IA (Mistral)']:.1f}s)", state="complete")

        # Paso 4 — Predicción ML
        with st.status("Prediciendo nivel Manchester (ML)...", expanded=True) as s:
            t0             = time.time()
            entidades_norm = datos.get("entidades_normalizadas", [])
            score_ansiedad = float(datos.get("score_ansiedad", 0.0))

            db_update(guid, inicio_entrenamiento=datetime.now())
            prediccion_ml = predecir(entidades_norm, score_ansiedad)

            tiempos["Predicción ML"] = time.time() - t0
            db_update(guid,
                fin_entrenamiento=datetime.now(),
                fin_solicitud=datetime.now(),
                estado="PREDICCION_COMPLETADA",
            )
            s.update(label=f"✓ Predicción ML completada ({tiempos['Predicción ML']:.3f}s)", state="complete")

        # Guardar JSON enriquecido en MinIO
        resultado_json = {
            "guid":                   guid,
            "origen":                 "Simulación",
            "texto":                  transcripcion,
            "resumen":                datos.get("resumen_es", ""),
            "entidades":              datos.get("entidades_extraidas", []),
            "entidades_normalizadas": entidades_norm,
            "etiqueta":               datos.get("triage_real", ""),
            "razonamiento":           datos.get("justificacion", ""),
            "score_ansiedad":         score_ansiedad,
            "prediccion_entrenada":   prediccion_ml,
        }
        json_bytes = json.dumps(resultado_json, ensure_ascii=False).encode("utf-8")
        minio.put_object(BUCKET_ENRIQUECIDOS, f"{guid}.json", BytesIO(json_bytes), len(json_bytes))

        # ── Resultado principal ────────────────────────────────────────────────
        st.divider()

        nivel_llm   = datos.get("triage_real", "")
        nivel_final = nivel_llm if nivel_llm in MANCHESTER else prediccion_ml
        cfg         = MANCHESTER.get(nivel_final, MANCHESTER["C3"])

        st.markdown(f"""
        <div style="
            background:{cfg['bg']};
            border:4px solid {cfg['color']};
            border-radius:12px;
            padding:28px 20px;
            text-align:center;
            margin:8px 0 16px 0;
        ">
            <div style="font-size:4em;font-weight:900;color:{cfg['color']};line-height:1">{nivel_final}</div>
            <div style="font-size:1.6em;font-weight:700;color:{cfg['color']};margin:6px 0">{cfg['label']}</div>
            <div style="color:#555;font-size:1.05em">{cfg['desc']}</div>
            <div style="color:#777;margin-top:8px">Tiempo máximo de atención: <b>{cfg['tiempo']}</b></div>
        </div>
        """, unsafe_allow_html=True)

        col1, col2, col3 = st.columns(3)
        col1.metric("Diagnóstico LLM", nivel_llm)
        col2.metric("Predicción ML",   prediccion_ml)
        col3.metric("Score ansiedad",  f"{score_ansiedad:.2f}")

        st.subheader("Resumen clínico")
        st.write(datos.get("resumen_es", "—"))

        st.subheader("Justificación clínica")
        st.info(datos.get("justificacion", "—"))

        col_a, col_b = st.columns(2)
        with col_a:
            st.subheader("Síntomas detectados")
            for e in datos.get("entidades_extraidas", []):
                st.write(f"• {e}")
        with col_b:
            st.subheader("Términos clínicos")
            for e in entidades_norm:
                st.write(f"• {e}")

        st.subheader("Tiempos por fase")
        rows = [{"Fase": k, "Tiempo (s)": f"{v:.2f}"} for k, v in tiempos.items()]
        rows.append({"Fase": "TOTAL", "Tiempo (s)": f"{sum(tiempos.values()):.2f}"})
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

        st.caption(f"Caso: `{guid}` | Audio: `{url_audio}` | Estado: PREDICCION_COMPLETADA")
