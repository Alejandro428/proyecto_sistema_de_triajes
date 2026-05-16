"""
TriageIA — Frontend Streamlit
Pestañas: Nuevo Triaje | Historial | Métricas Pipeline | Métricas Modelo
"""

import json
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from io import BytesIO
from zoneinfo import ZoneInfo

import httpx
import pandas as pd
import psycopg2
import streamlit as st

from components.db_queries import get_historial, get_stats, get_pipeline_timing
from components.minio_helpers import (
    descargar_imagen,
    descargar_json,
    descargar_modelo,
    get_minio,
    subir_audio,
    subir_json_enriquecido,
    subir_texto,
)

# ── Config ───────────────────────────────────────────────────────────────────
DATABASE_URL    = os.getenv("DATABASE_URL",    "")
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "")
MISTRAL_URL     = "https://api.mistral.ai/v1/chat/completions"

_MADRID = ZoneInfo("Europe/Madrid")


def _to_madrid(dt) -> str:
    if dt is None:
        return "—"
    try:
        if pd.isna(dt):
            return "—"
    except (TypeError, ValueError):
        pass
    if hasattr(dt, "tzinfo") and dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_MADRID).strftime("%d/%m/%Y %H:%M")


def _fmt_dur(secs) -> str:
    try:
        s = float(secs)
        if pd.isna(s):
            return "—"
    except (TypeError, ValueError):
        return "—"
    if s >= 3600:
        return f"{int(s // 3600)}h {int((s % 3600) // 60)}m"
    if s >= 60:
        return f"{int(s // 60)}m {int(s % 60)}s"
    return f"{s:.1f}s"

MANCHESTER = {
    "C1": {"color": "#B71C1C", "bg": "#FFEBEE", "badge": "#FFCDD2", "label": "EMERGENCIA",    "tiempo": "0 min",   "desc": "Riesgo vital inmediato"},
    "C2": {"color": "#E65100", "bg": "#FFF3E0", "badge": "#FFE0B2", "label": "MUY URGENTE",   "tiempo": "10 min",  "desc": "Atención máx. 10 minutos"},
    "C3": {"color": "#F57F17", "bg": "#FFFDE7", "badge": "#FFF9C4", "label": "URGENTE",       "tiempo": "60 min",  "desc": "Atención máx. 1 hora"},
    "C4": {"color": "#1B5E20", "bg": "#E8F5E9", "badge": "#C8E6C9", "label": "MENOS URGENTE", "tiempo": "120 min", "desc": "Atención máx. 2 horas"},
    "C5": {"color": "#0D47A1", "bg": "#E3F2FD", "badge": "#BBDEFB", "label": "NO URGENTE",    "tiempo": "240 min", "desc": "Atención máx. 4 horas"},
}

ESTADO_COLOR = {
    "MODELO_ENTRENADO":      "🟢",
    "DATASET_GENERADO":      "🟡",
    "SCORE_CALCULADO":       "🟡",
    "PROCESANDO":            "🔵",
    "INGESTED":              "⚪",
    "PREDICCION_COMPLETADA": "🟢",
    "ERROR":                 "🔴",
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
- "can't breathe", "short of breath", "falta de aire" → Disnea → C1 (súbita) / C2 (aguda)
- "wheezing", "whistling", "silbidos al respirar" → Sibilancias → C2 / C3
- "chest pressure", "like an elephant on my chest" → Dolor Torácico Opresivo → C2
- "fever", "high temperature", "fiebre" → Fiebre/Hipertermia → C3
- "passed out", "fainted", "lost consciousness" → Síncope → C1
- "swollen", "swelling", "hinchado" → Edema/Inflamación → C4
- "sharp chest pain", "stabbing chest pain" → Dolor Torácico Agudo → C2 / C3
- "cough", "tos" → Tos → C3 / C4 según severidad
- "nausea", "vomiting", "náuseas" → Náuseas/Vómitos → C3 / C4
- "muscle pain", "joint pain", "back pain" → Dolor Musculoesquelético → C4
- "dizziness", "mareo" → Mareo/Vértigo → C3 / C4
- "fatigue", "cansancio" → Fatiga → C4 / C5

## REGLA CRÍTICA
La clínica siempre prevalece sobre el estado emocional.
El score_ansiedad es solo informativo y NUNCA determina el nivel de triaje.

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


# ── Utilidades ────────────────────────────────────────────────────────────────

def _db():
    return psycopg2.connect(DATABASE_URL)


def _db_crear(guid: str, url_audio: str) -> None:
    with _db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO entrevista
                    (guid_entrevista, url_texto_original, inicio_solicitud, motor_workflow, estado)
                VALUES (%s, %s, %s, 'Streamlit', 'PREDICIENDO')
                ON CONFLICT (guid_entrevista) DO NOTHING
            """, (guid, url_audio, datetime.now()))


def _db_update(guid: str, **kw) -> None:
    if not kw:
        return
    sets   = ", ".join(f"{k} = %s" for k in kw)
    params = list(kw.values()) + [guid]
    with _db() as conn:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE entrevista SET {sets} WHERE guid_entrevista = %s", params)


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
    m = re.search(r"\{.*\}", sin_md, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    raise ValueError(f"JSON inválido: {texto[:200]}")


def _llamar_mistral(texto: str) -> tuple[dict, float]:
    t0   = time.time()
    resp = httpx.post(
        MISTRAL_URL,
        headers={"Authorization": f"Bearer {MISTRAL_API_KEY}", "Content-Type": "application/json"},
        json={"model": "mistral-large-latest", "temperature": 0.1,
              "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                           {"role": "user",   "content": texto}]},
        timeout=60.0,
    )
    resp.raise_for_status()
    return _extraer_json(resp.json()["choices"][0]["message"]["content"]), time.time() - t0


def _predecir_ml(entidades: list, score: float) -> str:
    artefacto = descargar_modelo()
    if artefacto is None:
        return "N/A"
    try:
        import numpy as np
        vec   = artefacto["vectorizador"]
        clf   = artefacto["modelo"]
        texto = " ".join(entidades)
        X_vec = vec.transform([texto]).toarray()
        X     = np.hstack([X_vec, [[score]]])
        return clf.predict(X)[0]
    except Exception:
        return "N/A"


def _manchester_card(nivel: str, nivel_llm: str, nivel_ml: str, score: float) -> None:
    cfg = MANCHESTER.get(nivel, MANCHESTER["C3"])
    cls = f"mcard-{nivel.lower()}" if nivel in MANCHESTER else "mcard-c3"
    discrepancia = nivel_llm and nivel_ml != "N/A" and nivel_llm != nivel_ml

    st.markdown(f"""
    <div class="manchester-card {cls}">
        <div class="m-badge">{nivel}</div>
        <div>
            <div class="m-label">{cfg['label']}</div>
            <div class="m-desc">{cfg['desc']}</div>
            <div class="m-time">⏱ Tiempo máximo de atención: <strong>{cfg['tiempo']}</strong></div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    col1, col2, col3 = st.columns(3)
    col1.metric("Diagnóstico LLM", nivel_llm or "—")
    col2.metric("Predicción ML",   nivel_ml)
    col3.metric("Score ansiedad",  f"{score:.2f}")

    if discrepancia:
        urgencia_llm = ["C1","C2","C3","C4","C5"].index(nivel_llm) if nivel_llm in MANCHESTER else 4
        urgencia_ml  = ["C1","C2","C3","C4","C5"].index(nivel_ml)  if nivel_ml  in MANCHESTER else 4
        if urgencia_ml > urgencia_llm:
            st.error(
                f"⚠️ **Posible under-triage**: el LLM asigna **{nivel_llm}** "
                f"pero el modelo ML predice **{nivel_ml}** (menos urgente). "
                "Revisar manualmente."
            )
        else:
            st.warning(f"ℹ️ Discrepancia LLM ({nivel_llm}) vs ML ({nivel_ml}). El nivel mostrado es el del LLM.")


def _info_card(icon: str, title: str, body: str) -> str:
    return f"""<div class="info-card">
        <div class="info-card-title">{icon}&nbsp; {title}</div>
        <div class="info-card-body">{body}</div>
    </div>"""


def _tag_list(items: list) -> str:
    if not items:
        return '<span style="color:var(--c-muted);font-style:italic">Sin datos</span>'
    return "".join(f'<span class="tag">{e}</span>' for e in items)


# ── Layout ───────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="TriageIA",
    page_icon="🏥",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
/* ── Base ────────────────────────────────────────────────── */
.main .block-container { padding-top: 1.2rem; max-width: 1150px; }

/* ── Header (gradiente azul fijo, funciona en ambos modos) ── */
.triage-header {
    background: linear-gradient(135deg, #1565C0 0%, #0D47A1 55%, #1976D2 100%);
    border-radius: 12px;
    padding: 22px 28px;
    margin-bottom: 18px;
    display: flex;
    align-items: center;
    gap: 18px;
}
.triage-header h1 {
    color: #fff !important;
    font-size: 1.9rem !important;
    font-weight: 800 !important;
    margin: 0 !important;
    text-shadow: 0 1px 4px rgba(0,0,0,0.35);
}
.triage-header .sub { color: #BBDEFB; font-size: 0.88rem; margin-top: 4px; }

/* ── Tabs ── usa variables nativas de Streamlit ───────────── */
.stTabs [data-baseweb="tab-list"] {
    gap: 6px;
    background: var(--secondary-background-color);
    border-radius: 10px;
    padding: 6px;
}
.stTabs [data-baseweb="tab"] {
    padding: 8px 22px;
    border-radius: 7px;
    font-weight: 600;
    font-size: 0.93rem;
    color: var(--primary-color);
    background: transparent;
    border: none;
}
.stTabs [aria-selected="true"] {
    background: var(--background-color) !important;
    color: var(--primary-color) !important;
    box-shadow: 0 2px 8px rgba(0,0,0,0.18);
}

/* ── Metric cards ─────────────────────────────────────────── */
div[data-testid="metric-container"] {
    background: var(--background-color);
    border: 1px solid rgba(128,128,128,0.22);
    border-top: 4px solid var(--primary-color);
    border-radius: 10px;
    padding: 14px 18px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.07);
}
div[data-testid="metric-container"] label {
    color: var(--primary-color) !important;
    font-weight: 700 !important;
    font-size: 0.80rem !important;
    text-transform: uppercase;
    letter-spacing: 0.06em;
}
div[data-testid="metric-container"] [data-testid="stMetricValue"] {
    color: var(--text-color) !important;
    font-size: 1.55rem !important;
    font-weight: 800 !important;
}

/* ── Headers ──────────────────────────────────────────────── */
h3 {
    color: var(--primary-color) !important;
    border-bottom: 2px solid var(--secondary-background-color);
    padding-bottom: 6px;
    margin-bottom: 16px !important;
}
h4 { color: var(--primary-color) !important; }

/* ── Botón primario ───────────────────────────────────────── */
.stButton > button[kind="primary"] {
    background: linear-gradient(135deg, #1565C0, #0D47A1);
    color: white !important;
    border: none;
    border-radius: 8px;
    font-weight: 700;
    padding: 10px 20px;
    transition: opacity 0.2s;
}
.stButton > button[kind="primary"]:hover { opacity: 0.87; }

/* ── Divider ──────────────────────────────────────────────── */
hr { border-color: rgba(128,128,128,0.2) !important; margin: 18px 0 !important; }

/* ── Info cards (detalles de caso) ───────────────────────── */
.info-card {
    background: var(--background-color);
    border: 1.5px solid rgba(128,128,128,0.22);
    border-left: 5px solid var(--primary-color);
    border-radius: 0 10px 10px 0;
    padding: 16px 20px 16px 18px;
    margin-bottom: 14px;
    box-shadow: 0 3px 10px rgba(0,0,0,0.07);
}
.info-card-title {
    font-size: 0.74rem;
    font-weight: 800;
    color: var(--primary-color);
    text-transform: uppercase;
    letter-spacing: 0.09em;
    margin-bottom: 10px;
    padding-bottom: 7px;
    border-bottom: 1.5px solid rgba(128,128,128,0.18);
    display: flex;
    align-items: center;
    gap: 6px;
}
.info-card-body {
    color: var(--text-color);
    font-size: 0.94rem;
    line-height: 1.65;
}

/* ── Tags (síntomas / entidades) ─────────────────────────── */
.tag {
    display: inline-block;
    background: var(--secondary-background-color);
    color: var(--primary-color);
    border: 1.5px solid rgba(128,128,128,0.25);
    border-radius: 20px;
    padding: 4px 13px;
    margin: 3px;
    font-size: 0.84rem;
    font-weight: 600;
}

/* ── Manchester cards ─────────────────────────────────────── */
.manchester-card {
    border-left: 8px solid;
    border-radius: 10px;
    padding: 20px 24px;
    margin: 12px 0 20px 0;
    display: flex;
    align-items: center;
    gap: 18px;
}
.m-badge {
    color: white;
    font-size: 2.2em;
    font-weight: 900;
    border-radius: 8px;
    padding: 8px 18px;
    min-width: 80px;
    text-align: center;
    flex-shrink: 0;
}
.m-label { font-size: 1.35em; font-weight: 700; }
.m-desc  { margin-top: 3px; color: var(--text-color); opacity: 0.72; }
.m-time  { color: var(--text-color); opacity: 0.58; font-size: 0.9em; margin-top: 5px; }

/* rgba funciona igual en light y dark — 12% de opacidad */
.mcard-c1 { background: rgba(183,28,28,0.12);  border-left-color: #E53935; }
.mcard-c1 .m-badge { background: #C62828; }
.mcard-c1 .m-label { color: #E53935; }

.mcard-c2 { background: rgba(230,81,0,0.12);   border-left-color: #EF6C00; }
.mcard-c2 .m-badge { background: #E64A19; }
.mcard-c2 .m-label { color: #EF6C00; }

.mcard-c3 { background: rgba(245,127,23,0.12); border-left-color: #FB8C00; }
.mcard-c3 .m-badge { background: #F57F17; }
.mcard-c3 .m-label { color: #FB8C00; }

.mcard-c4 { background: rgba(27,94,32,0.12);   border-left-color: #43A047; }
.mcard-c4 .m-badge { background: #2E7D32; }
.mcard-c4 .m-label { color: #43A047; }

.mcard-c5 { background: rgba(21,101,192,0.12); border-left-color: #1E88E5; }
.mcard-c5 .m-badge { background: #1565C0; }
.mcard-c5 .m-label { color: #1E88E5; }
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="triage-header">
    <div style="font-size:3rem;line-height:1">🏥</div>
    <div>
        <h1>TriageIA — Sistema de Triaje Manchester</h1>
        <div class="sub">Protocolo Manchester C1–C5 &nbsp;·&nbsp; Dataset Fareez (270 entrevistas OSCE)
        &nbsp;·&nbsp; Mistral + TF-IDF + Logistic Regression</div>
    </div>
</div>
""", unsafe_allow_html=True)

tab_triaje, tab_historial, tab_pipeline, tab_modelo = st.tabs([
    "🩺 Nuevo Triaje",
    "📋 Historial",
    "📊 Métricas del Pipeline",
    "🤖 Métricas del Modelo",
])


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — Nuevo Triaje
# ══════════════════════════════════════════════════════════════════════════════
with tab_triaje:
    st.markdown("### Analizar nueva entrevista clínica")
    st.markdown("Sube el audio de la entrevista y el sistema realizará el triaje automático.")

    uploaded = st.file_uploader(
        "Audio de la entrevista",
        type=["mp3", "wav", "m4a", "ogg", "webm"],
        help="Formatos soportados: MP3, WAV, M4A, OGG, WEBM",
    )

    if uploaded:
        st.audio(uploaded)
        col_btn, col_info = st.columns([2, 5])
        with col_btn:
            analizar = st.button("Analizar entrevista", type="primary", use_container_width=True)
        with col_info:
            st.info("El análisis tarda ~15-30 segundos según la duración del audio.")

        if analizar:
            guid        = f"PAC{datetime.now().strftime('%Y%m%d%H%M%S')}"
            ext         = uploaded.name.rsplit(".", 1)[-1].lower()
            audio_bytes = uploaded.getvalue()
            tiempos     = {}

            # Paso 1 — MinIO
            with st.status("💾 Guardando audio...", expanded=False) as s:
                url_audio = subir_audio(guid, audio_bytes, ext)
                _db_crear(guid, url_audio)
                s.update(label="✓ Audio guardado en MinIO", state="complete")

            # Paso 2 — Whisper
            with st.status("🎙️ Transcribiendo audio con Whisper...", expanded=True) as s:
                t0 = time.time()
                _db_update(guid, inicio_preprocesamiento=datetime.now())
                import whisper
                model = whisper.load_model("base")
                with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as tmp:
                    tmp.write(audio_bytes)
                    tmp_path = tmp.name
                try:
                    transcripcion = model.transcribe(tmp_path)["text"].strip()
                finally:
                    os.unlink(tmp_path)
                subir_texto(guid, transcripcion)
                tiempos["Transcripción (Whisper)"] = time.time() - t0
                _db_update(guid, fin_preprocesamiento=datetime.now())
                s.update(label=f"✓ Transcripción completada ({tiempos['Transcripción (Whisper)']:.1f}s)", state="complete")

            with st.expander("📄 Ver transcripción completa"):
                st.write(transcripcion)

            # Paso 3 — Mistral
            with st.status("🧠 Analizando con Mistral IA...", expanded=True) as s:
                t0 = time.time()
                _db_update(guid, inicio_extraccion_entidades=datetime.now())
                datos, dur_llm = _llamar_mistral(transcripcion)
                fin_llm        = datetime.now()
                tiempos["Análisis IA (Mistral)"] = dur_llm
                _db_update(
                    guid,
                    fin_extraccion_entidades=fin_llm,
                    inicio_normalizacion=fin_llm, fin_normalizacion=fin_llm,
                    inicio_etiquetado=fin_llm,    fin_etiquetado=fin_llm,
                    inicio_score=fin_llm,         fin_score=fin_llm,
                )
                s.update(label=f"✓ Análisis IA completado ({dur_llm:.1f}s)", state="complete")

            # Paso 4 — ML
            with st.status("⚙️ Predicción con modelo ML...", expanded=True) as s:
                t0             = time.time()
                entidades_norm = datos.get("entidades_normalizadas", [])
                score_ans      = float(datos.get("score_ansiedad", 0.0))
                _db_update(guid, inicio_entrenamiento=datetime.now())
                pred_ml = _predecir_ml(entidades_norm, score_ans)
                tiempos["Predicción ML"] = time.time() - t0
                _db_update(
                    guid,
                    fin_entrenamiento=datetime.now(),
                    fin_solicitud=datetime.now(),
                    estado="PREDICCION_COMPLETADA",
                )
                s.update(label=f"✓ Predicción ML completada ({tiempos['Predicción ML']:.3f}s)", state="complete")

            # Guardar JSON enriquecido
            resultado = {
                "guid": guid, "origen": "Simulación", "texto": transcripcion,
                "resumen": datos.get("resumen_es", ""),
                "entidades": datos.get("entidades_extraidas", []),
                "entidades_normalizadas": entidades_norm,
                "etiqueta": datos.get("triage_real", ""),
                "razonamiento": datos.get("justificacion", ""),
                "score_ansiedad": score_ans,
                "prediccion_entrenada": pred_ml,
            }
            subir_json_enriquecido(guid, resultado)

            # ── Resultado ──────────────────────────────────────────────────
            st.divider()
            nivel_llm = datos.get("triage_real", "")
            nivel_fin = nivel_llm if nivel_llm in MANCHESTER else pred_ml
            _manchester_card(nivel_fin, nivel_llm, pred_ml, score_ans)

            col_a, col_b = st.columns(2)
            with col_a:
                st.markdown(
                    _info_card("📋", "Resumen clínico", datos.get("resumen_es", "—")),
                    unsafe_allow_html=True,
                )
                st.markdown(
                    _info_card("⚖️", "Justificación clínica", datos.get("justificacion", "—")),
                    unsafe_allow_html=True,
                )
            with col_b:
                st.markdown(
                    _info_card("🔍", "Síntomas detectados", _tag_list(datos.get("entidades_extraidas", []))),
                    unsafe_allow_html=True,
                )
                st.markdown(
                    _info_card("🏥", "Términos clínicos normalizados", _tag_list(entidades_norm)),
                    unsafe_allow_html=True,
                )

            st.divider()
            st.markdown("#### ⏱ Tiempos por fase")
            tcols = st.columns(len(tiempos))
            for i, (fase, t) in enumerate(tiempos.items()):
                tcols[i].metric(fase, _fmt_dur(t))
            total = sum(tiempos.values())
            st.success(f"**Tiempo total del análisis: {_fmt_dur(total)}**")
            st.caption(f"Caso: `{guid}` · Estado: PREDICCION_COMPLETADA")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — Historial
# ══════════════════════════════════════════════════════════════════════════════
with tab_historial:
    st.markdown("### Historial de entrevistas procesadas")

    col_f1, col_f2, col_f3 = st.columns([2, 2, 1])
    with col_f3:
        if st.button("🔄 Actualizar", use_container_width=True):
            get_historial.clear()
            get_stats.clear()

    df_hist = get_historial(300)

    if df_hist.empty:
        st.info("No hay entrevistas en la base de datos todavía.")
    else:
        # Filtros
        with col_f1:
            estados = ["Todos"] + sorted(df_hist["estado"].dropna().unique().tolist())
            filtro_estado = st.selectbox("Filtrar por estado", estados)
        with col_f2:
            filtro_guid = st.text_input("Buscar por GUID", placeholder="Ej: RES0001, PAC2026…")

        df_show = df_hist.copy()
        if filtro_estado != "Todos":
            df_show = df_show[df_show["estado"] == filtro_estado]
        if filtro_guid.strip():
            df_show = df_show[df_show["guid"].str.contains(filtro_guid.strip(), case=False, na=False)]

        # Añadir emoji de estado
        df_show[""] = df_show["estado"].map(lambda x: ESTADO_COLOR.get(x, "⚪"))

        df_display = df_show[["", "guid", "estado", "inicio_solicitud", "dur_e2e_seg", "dur_llm_seg"]].copy()
        df_display["inicio_solicitud"] = df_display["inicio_solicitud"].apply(_to_madrid)
        df_display["dur_e2e_seg"]      = df_display["dur_e2e_seg"].apply(_fmt_dur)
        df_display["dur_llm_seg"]      = df_display["dur_llm_seg"].apply(_fmt_dur)

        st.dataframe(
            df_display.rename(columns={
                "guid":             "GUID",
                "estado":           "Estado",
                "inicio_solicitud": "Fecha ingesta (Madrid)",
                "dur_e2e_seg":      "E2E",
                "dur_llm_seg":      "LLM",
            }),
            hide_index=True,
            use_container_width=True,
            height=420,
        )

        st.caption(f"Mostrando {len(df_show)} de {len(df_hist)} entrevistas")

        # Detalle de caso seleccionado
        st.divider()
        st.markdown("#### 🔎 Detalle de caso")
        guid_sel = st.selectbox(
            "Selecciona un GUID para ver el detalle",
            options=["—"] + df_show["guid"].tolist(),
        )
        if guid_sel and guid_sel != "—":
            datos_json = descargar_json(guid_sel)
            if datos_json:
                nivel = datos_json.get("etiqueta", "")
                pred  = datos_json.get("prediccion_entrenada", "N/A") or "N/A"
                score = float(datos_json.get("score_ansiedad", 0))
                _manchester_card(nivel, nivel, pred, score)

                c1, c2 = st.columns(2)
                with c1:
                    st.markdown(
                        _info_card("📋", "Resumen clínico", datos_json.get("resumen", "—")),
                        unsafe_allow_html=True,
                    )
                    st.markdown(
                        _info_card("⚖️", "Justificación Manchester", datos_json.get("razonamiento", "—")),
                        unsafe_allow_html=True,
                    )
                with c2:
                    st.markdown(
                        _info_card("🔍", "Síntomas detectados", _tag_list(datos_json.get("entidades", []))),
                        unsafe_allow_html=True,
                    )
                    st.markdown(
                        _info_card("🏥", "Términos clínicos normalizados", _tag_list(datos_json.get("entidades_normalizadas", []))),
                        unsafe_allow_html=True,
                    )
            else:
                st.warning(f"No hay JSON enriquecido para `{guid_sel}` en MinIO.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — Métricas del Pipeline
# ══════════════════════════════════════════════════════════════════════════════
with tab_pipeline:
    st.markdown("### Métricas del pipeline de procesamiento")

    stats = get_stats()
    if not stats or not stats.get("total"):
        st.info("No hay datos de métricas todavía. Lanza dag_ingestion para comenzar.")
    else:
        # KPIs
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Total entrevistas",    int(stats.get("total", 0) or 0))
        c2.metric("Completadas",          int(stats.get("completados", 0) or 0))
        c3.metric("Errores",              int(stats.get("errores", 0) or 0))
        c4.metric("Tiempo medio LLM",     f"{stats.get('avg_llm_seg') or 0:.1f}s")
        c5.metric("Latencia media E2E",   f"{stats.get('avg_e2e_seg') or 0:.1f}s")

        st.divider()

        df_hist = get_historial(300)
        if not df_hist.empty and "estado" in df_hist.columns:
            col_g1, col_g2 = st.columns(2)

            with col_g1:
                st.markdown("#### Distribución de estados")
                dist_estado = df_hist["estado"].value_counts().reset_index()
                dist_estado.columns = ["Estado", "Casos"]
                st.bar_chart(dist_estado.set_index("Estado"))

            with col_g2:
                st.markdown("#### Latencia LLM por caso (últimos 100)")
                df_timing = get_pipeline_timing()
                if not df_timing.empty and "llm_seg" in df_timing.columns:
                    df_timing_plot = df_timing[["guid", "llm_seg"]].dropna().head(100)
                    st.bar_chart(df_timing_plot.set_index("guid"))
                else:
                    st.info("Sin datos de timing LLM aún.")

        st.divider()
        st.markdown("#### Tiempos medios por etapa del pipeline")
        df_timing = get_pipeline_timing()
        if not df_timing.empty:
            avg_prep  = df_timing["prep_seg"].dropna().mean()
            avg_llm   = df_timing["llm_seg"].dropna().mean()
            avg_train = df_timing["train_seg"].dropna().mean()
            t1, t2, t3 = st.columns(3)
            t1.metric("⚙️ Preprocesamiento medio", _fmt_dur(avg_prep))
            t2.metric("🧠 LLM medio por caso",     _fmt_dur(avg_llm))
            t3.metric("🏋️ Entrenamiento total",    _fmt_dur(avg_train))

            with st.expander("Ver datos detallados por caso"):
                df_det = df_timing.copy()
                df_det["prep_seg"]  = df_det["prep_seg"].apply(_fmt_dur)
                df_det["llm_seg"]   = df_det["llm_seg"].apply(_fmt_dur)
                df_det["train_seg"] = df_det["train_seg"].apply(_fmt_dur)
                st.dataframe(
                    df_det.rename(columns={
                        "guid":      "GUID",
                        "prep_seg":  "Preprocesamiento",
                        "llm_seg":   "LLM",
                        "train_seg": "Entrenamiento",
                    }),
                    hide_index=True,
                    use_container_width=True,
                    height=320,
                )
        else:
            st.info("Sin datos de timing todavía.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — Métricas del Modelo
# ══════════════════════════════════════════════════════════════════════════════
with tab_modelo:
    st.markdown("### Métricas del modelo de Machine Learning")

    artefacto = descargar_modelo()

    if artefacto is None:
        st.warning(
            "No hay modelo entrenado en MinIO todavía. "
            "Completa el pipeline de Fase 1 (dag_ingestion → dag_llm_enrichment → dag_model_training)."
        )
    else:
        metricas = artefacto.get("metricas", {})

        # KPIs del modelo
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Accuracy",       f"{metricas.get('accuracy',        0):.2%}")
        c2.metric("Precision macro",f"{metricas.get('precision_macro', 0):.2%}")
        c3.metric("Recall macro",   f"{metricas.get('recall_macro',    0):.2%}")
        c4.metric("F1 macro",       f"{metricas.get('f1_macro',        0):.2%}")
        c5.metric("Total dataset",  int(metricas.get("n_casos", 0)))

        if metricas.get("train_segundos"):
            st.caption(f"⏱ Tiempo de entrenamiento: {metricas['train_segundos']:.1f}s")

        st.divider()

        # Gráficas desde MinIO
        img_dist = descargar_imagen("grafica_distribucion.png")
        img_conf = descargar_imagen("grafica_confusion.png")
        img_imp  = descargar_imagen("grafica_importancia.png")

        if img_dist or img_conf:
            col_d, col_c = st.columns([3, 2])
            with col_d:
                if img_dist:
                    st.markdown("#### Distribución de niveles Manchester")
                    st.image(img_dist, use_container_width=True)
            with col_c:
                if img_conf:
                    st.markdown("#### Matriz de confusión (test 20%)")
                    st.image(img_conf, use_container_width=True)

        if img_imp:
            st.markdown("#### Top 15 términos clínicos — coeficientes LR por nivel")
            st.image(img_imp, use_container_width=True)

        st.divider()

        # Under-triage en historial — carga desde el CSV (una sola descarga)
        st.markdown("#### ⚠️ Detección de under-triage en el historial")
        try:
            from io import BytesIO
            csv_bytes = get_minio().get_object("datasets", "dataset_entrenamiento.csv").read()
            df_csv    = pd.read_csv(BytesIO(csv_bytes))
            niveles   = ["C1", "C2", "C3", "C4", "C5"]
            df_csv    = df_csv[df_csv["etiqueta"].isin(niveles) & df_csv["prediccion_entrenada"].isin(niveles)]
            df_csv["idx_llm"] = df_csv["etiqueta"].map(niveles.index)
            df_csv["idx_ml"]  = df_csv["prediccion_entrenada"].map(niveles.index)
            df_ut = df_csv[df_csv["idx_ml"] > df_csv["idx_llm"]][[
                "guid", "etiqueta", "prediccion_entrenada"
            ]].copy()
            df_ut["Diferencia"] = (df_csv.loc[df_ut.index, "idx_ml"] - df_csv.loc[df_ut.index, "idx_llm"])
            df_ut = df_ut.rename(columns={"guid": "GUID", "etiqueta": "LLM (real)", "prediccion_entrenada": "ML (predicho)"})
            df_ut = df_ut.sort_values("Diferencia", ascending=False)

            if not df_ut.empty:
                st.error(f"Se detectaron {len(df_ut)} casos de posible under-triage:")
                st.dataframe(df_ut[["GUID","LLM (real)","ML (predicho)","Diferencia"]], hide_index=True, use_container_width=True)
            else:
                st.success("No se detectaron casos de under-triage en el historial.")
        except Exception as e:
            st.info(f"Dataset no disponible todavía: {e}")

        st.divider()

        # ── Evaluación Fase 2 ─────────────────────────────────────────────────
        st.markdown("#### 🎯 Evaluación Fase 2 — predicción sobre audios nuevos")
        try:
            from io import BytesIO
            raw = get_minio().get_object("modelos", "evaluacion_fase2.json").read()
            evaluacion = json.loads(raw)

            mg = evaluacion.get("metricas_globales", {})
            tc = evaluacion.get("triaje_clinico", {})

            f1c, f2c, f3c, f4c = st.columns(4)
            f1c.metric("Accuracy",         f"{mg.get('accuracy', 0):.2%}")
            f2c.metric("Recall macro",     f"{mg.get('recall_macro', 0):.2%}")
            f3c.metric("F1 macro",         f"{mg.get('f1_macro', 0):.2%}")
            f4c.metric("Casos evaluados",  int(evaluacion.get("n_casos_validos", 0)))

            t1c, t2c, t3c = st.columns(3)
            t1c.metric("✅ Correctos",     int(tc.get("correctos", 0)))
            t2c.metric("⚠️ Over-triage",   int(tc.get("over_triage", 0)))
            t3c.metric("🚨 Under-triage",  int(tc.get("under_triage", 0)))

            # Matriz de confusión
            img_eval = descargar_imagen("evaluacion_fase2_confusion.png")
            if img_eval:
                _, c_center, _ = st.columns([1, 3, 1])
                with c_center:
                    st.image(img_eval, caption="Matriz de confusión Fase 2", use_container_width=True)

            # Detalle por caso
            por_caso = evaluacion.get("por_caso", [])
            if por_caso:
                with st.expander(f"Ver detalle por caso ({len(por_caso)} casos)"):
                    df_eval = pd.DataFrame(por_caso)
                    st.dataframe(
                        df_eval.rename(columns={
                            "guid":          "GUID",
                            "etiqueta":      "Etiqueta LLM",
                            "prediccion":    "Predicción ML",
                            "valoracion":    "Valoración",
                            "score":         "Score",
                            "error_niveles": "Δ niveles",
                        }),
                        hide_index=True,
                        use_container_width=True,
                        height=320,
                    )

            fecha = evaluacion.get("fecha_evaluacion", "")
            if fecha:
                st.caption(f"Última evaluación: {fecha}")

        except Exception as e:
            st.info(
                "No hay evaluación de Fase 2 todavía. "
                "Lanza `dag_prediction_phase_2` en Airflow con audios en `data/audios/`."
            )
