"""
TriageIA — Frontend Streamlit
Pestañas: Nuevo Triaje | Historial | Métricas Pipeline | Métricas Modelo
"""

import json
import os
import re
import tempfile
import time
from datetime import datetime
from io import BytesIO

import httpx
import pandas as pd
import psycopg2
import streamlit as st

from components.db_queries import get_historial, get_stats, get_pipeline_timing
from components.minio_helpers import (
    descargar_imagen,
    descargar_json,
    descargar_modelo,
    subir_audio,
    subir_json_enriquecido,
    subir_texto,
)

# ── Config ───────────────────────────────────────────────────────────────────
DATABASE_URL    = os.getenv("DATABASE_URL",    "")
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "")
MISTRAL_URL     = "https://api.mistral.ai/v1/chat/completions"

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
    discrepancia = nivel_llm and nivel_ml != "N/A" and nivel_llm != nivel_ml

    st.markdown(f"""
    <div style="
        background:{cfg['bg']};
        border-left:8px solid {cfg['color']};
        border-radius:10px;
        padding:24px 28px;
        margin:12px 0 20px 0;
    ">
        <div style="display:flex;align-items:center;gap:16px;">
            <div style="
                background:{cfg['color']};
                color:white;
                font-size:2.2em;
                font-weight:900;
                border-radius:8px;
                padding:8px 18px;
                min-width:80px;
                text-align:center;
            ">{nivel}</div>
            <div>
                <div style="font-size:1.4em;font-weight:700;color:{cfg['color']}">{cfg['label']}</div>
                <div style="color:#555;margin-top:2px">{cfg['desc']}</div>
                <div style="color:#777;font-size:0.92em;margin-top:4px">
                    ⏱ Tiempo máximo de atención: <strong>{cfg['tiempo']}</strong>
                </div>
            </div>
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


# ── Layout ───────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="TriageIA",
    page_icon="🏥",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
    .main .block-container { padding-top: 1.5rem; max-width: 1100px; }
    h1 { font-size: 1.8rem !important; }
    .stTabs [data-baseweb="tab-list"] { gap: 8px; }
    .stTabs [data-baseweb="tab"] {
        padding: 8px 20px;
        border-radius: 6px 6px 0 0;
        font-weight: 600;
    }
    div[data-testid="metric-container"] {
        background: #f8f9fa;
        border: 1px solid #e9ecef;
        border-radius: 8px;
        padding: 12px 16px;
    }
</style>
""", unsafe_allow_html=True)

st.markdown("## 🏥 TriageIA — Sistema de Triaje Manchester")
st.caption("Protocolo Manchester C1–C5 · Dataset Fareez (272 entrevistas OSCE) · Mistral + RandomForest")

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
                st.markdown("#### 📋 Resumen clínico")
                st.write(datos.get("resumen_es", "—"))
                st.markdown("#### ⚖️ Justificación clínica")
                st.info(datos.get("justificacion", "—"))

            with col_b:
                st.markdown("#### 🔍 Síntomas detectados")
                for e in datos.get("entidades_extraidas", []):
                    st.markdown(f"- {e}")
                st.markdown("#### 🏥 Términos clínicos normalizados")
                for e in entidades_norm:
                    st.markdown(f"- {e}")

            st.divider()
            st.markdown("#### ⏱ Tiempos por fase")
            rows = [{"Fase": k, "Tiempo (s)": f"{v:.3f}"} for k, v in tiempos.items()]
            rows.append({"Fase": "**TOTAL**", "Tiempo (s)": f"{sum(tiempos.values()):.3f}"})
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
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

        st.dataframe(
            df_show[["", "guid", "estado", "inicio_solicitud", "dur_e2e_seg", "dur_llm_seg"]].rename(columns={
                "guid":             "GUID",
                "estado":           "Estado",
                "inicio_solicitud": "Fecha ingesta",
                "dur_e2e_seg":      "E2E (s)",
                "dur_llm_seg":      "LLM (s)",
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
                    st.markdown("**Resumen clínico**")
                    st.write(datos_json.get("resumen", "—"))
                    st.markdown("**Justificación**")
                    st.info(datos_json.get("razonamiento", "—"))
                with c2:
                    st.markdown("**Entidades detectadas**")
                    for e in datos_json.get("entidades", []):
                        st.markdown(f"- {e}")
                    st.markdown("**Términos clínicos**")
                    for e in datos_json.get("entidades_normalizadas", []):
                        st.markdown(f"- {e}")
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
        st.markdown("#### Tabla de tiempos por etapa")
        df_timing = get_pipeline_timing()
        if not df_timing.empty:
            st.dataframe(
                df_timing.rename(columns={
                    "guid":      "GUID",
                    "prep_seg":  "Preprocesamiento (s)",
                    "llm_seg":   "LLM (s)",
                    "train_seg": "Entrenamiento (s)",
                }),
                hide_index=True,
                use_container_width=True,
                height=350,
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
        c5.metric("Casos train",    int(metricas.get("n_casos", 0)))

        if metricas.get("train_segundos"):
            st.caption(f"⏱ Tiempo de entrenamiento: {metricas['train_segundos']:.1f}s")

        st.divider()

        # Gráficas desde MinIO
        col_g1, col_g2 = st.columns(2)

        with col_g1:
            img = descargar_imagen("grafica_distribucion.png")
            if img:
                st.markdown("#### Distribución de niveles Manchester")
                st.image(img, use_column_width=True)

        with col_g2:
            img = descargar_imagen("grafica_confusion.png")
            if img:
                st.markdown("#### Matriz de confusión (test 20%)")
                st.image(img, use_column_width=True)

        img_imp = descargar_imagen("grafica_importancia.png")
        if img_imp:
            st.markdown("#### Top 15 términos clínicos más relevantes para el modelo")
            st.image(img_imp, use_column_width=True)

        st.divider()

        # Under-triage en historial
        st.markdown("#### ⚠️ Detección de under-triage en el historial")
        df_hist = get_historial(300)
        if not df_hist.empty:
            under_triage = []
            for _, row in df_hist.iterrows():
                datos_json = descargar_json(row["guid"])
                if not datos_json:
                    continue
                etiqueta = datos_json.get("etiqueta", "")
                pred_ml  = datos_json.get("prediccion_entrenada", "") or ""
                if not etiqueta or not pred_ml or pred_ml == "N/A":
                    continue
                niveles = ["C1","C2","C3","C4","C5"]
                if etiqueta in niveles and pred_ml in niveles:
                    if niveles.index(pred_ml) > niveles.index(etiqueta):
                        under_triage.append({
                            "GUID":          row["guid"],
                            "LLM (real)":    etiqueta,
                            "ML (predicho)": pred_ml,
                            "Diferencia":    niveles.index(pred_ml) - niveles.index(etiqueta),
                        })

            if under_triage:
                df_ut = pd.DataFrame(under_triage).sort_values("Diferencia", ascending=False)
                st.error(f"Se detectaron {len(df_ut)} casos de posible under-triage:")
                st.dataframe(df_ut, hide_index=True, use_container_width=True)
            else:
                st.success("No se detectaron casos de under-triage en el historial.")
        else:
            st.info("Sin historial disponible.")
