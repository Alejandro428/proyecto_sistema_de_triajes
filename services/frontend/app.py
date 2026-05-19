"""
TriageIA — Frontend Streamlit
Pestañas: Nuevo Triaje | Historial

El frontend NO procesa nada: solo sube el audio a la API y muestra
el resultado. Toda la lógica (Whisper + Mistral + Random Forest + BD)
vive en el servicio API (FastAPI).
"""

import os
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx
import pandas as pd
import streamlit as st

from components.db_queries import get_historial
from components.minio_helpers import descargar_json

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

API_URL = os.getenv("API_URL", "http://api:8000")
_MADRID = ZoneInfo("Europe/Madrid")


MANCHESTER = {
    "C1": {"color": "#B71C1C", "label": "EMERGENCIA",    "tiempo": "0 min",   "desc": "Riesgo vital inmediato"},
    "C2": {"color": "#E65100", "label": "MUY URGENTE",   "tiempo": "10 min",  "desc": "Atención máx. 10 minutos"},
    "C3": {"color": "#F57F17", "label": "URGENTE",       "tiempo": "60 min",  "desc": "Atención máx. 1 hora"},
    "C4": {"color": "#1B5E20", "label": "MENOS URGENTE", "tiempo": "120 min", "desc": "Atención máx. 2 horas"},
    "C5": {"color": "#0D47A1", "label": "NO URGENTE",    "tiempo": "240 min", "desc": "Atención máx. 4 horas"},
}

ESTADO_ICON = {
    "PREDICCION_COMPLETADA": "🟢",
    "PREDICIENDO":           "🔵",
    "ERROR":                 "🔴",
}


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS — formato y UI
# ─────────────────────────────────────────────────────────────────────────────

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
    if s >= 60:
        return f"{int(s // 60)}m {int(s % 60)}s"
    return f"{s:.2f}s"


def _info_card(icon: str, title: str, body: str) -> str:
    return f"""<div class="info-card">
        <div class="info-card-title">{icon}&nbsp; {title}</div>
        <div class="info-card-body">{body}</div>
    </div>"""


def _tag_list(items: list) -> str:
    if not items:
        return '<span style="color:var(--c-muted);font-style:italic">Sin datos</span>'
    return "".join(f'<span class="tag">{e}</span>' for e in items)


def _manchester_card(nivel: str) -> None:
    if nivel not in MANCHESTER:
        st.warning("⚠️ Modelo ML no disponible — no se pudo realizar la predicción.")
        return
    cfg = MANCHESTER[nivel]
    cls = f"mcard-{nivel.lower()}"
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


# ─────────────────────────────────────────────────────────────────────────────
# LAYOUT & ESTILOS
# ─────────────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="TriageIA",
    page_icon="🏥",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
.main .block-container { padding-top: 1.2rem; max-width: 1150px; }

.triage-header {
    background: linear-gradient(135deg, #1565C0 0%, #0D47A1 55%, #1976D2 100%);
    border-radius: 12px;
    padding: 22px 28px;
    margin-bottom: 18px;
    display: flex; align-items: center; gap: 18px;
}
.triage-header h1 {
    color: #fff !important;
    font-size: 1.9rem !important;
    font-weight: 800 !important;
    margin: 0 !important;
    text-shadow: 0 1px 4px rgba(0,0,0,0.35);
}
.triage-header .sub { color: #BBDEFB; font-size: 0.88rem; margin-top: 4px; }

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

h3 {
    color: var(--primary-color) !important;
    border-bottom: 2px solid var(--secondary-background-color);
    padding-bottom: 6px;
    margin-bottom: 16px !important;
}
h4 { color: var(--primary-color) !important; }

.stButton > button[kind="primary"] {
    background: linear-gradient(135deg, #1565C0, #0D47A1);
    color: white !important;
    border: none;
    border-radius: 8px;
    font-weight: 700;
    padding: 10px 20px;
}
.stButton > button[kind="primary"]:hover { opacity: 0.87; }

hr { border-color: rgba(128,128,128,0.2) !important; margin: 18px 0 !important; }

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
}
.info-card-body { color: var(--text-color); font-size: 0.94rem; line-height: 1.65; }

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

.manchester-card {
    border-left: 8px solid;
    border-radius: 10px;
    padding: 20px 24px;
    margin: 12px 0 20px 0;
    display: flex; align-items: center; gap: 18px;
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

.mcard-c1 { background: rgba(183,28,28,0.12);  border-left-color: #E53935; }
.mcard-c1 .m-badge { background: #C62828; } .mcard-c1 .m-label { color: #E53935; }
.mcard-c2 { background: rgba(230,81,0,0.12);   border-left-color: #EF6C00; }
.mcard-c2 .m-badge { background: #E64A19; } .mcard-c2 .m-label { color: #EF6C00; }
.mcard-c3 { background: rgba(245,127,23,0.12); border-left-color: #FB8C00; }
.mcard-c3 .m-badge { background: #F57F17; } .mcard-c3 .m-label { color: #FB8C00; }
.mcard-c4 { background: rgba(27,94,32,0.12);   border-left-color: #43A047; }
.mcard-c4 .m-badge { background: #2E7D32; } .mcard-c4 .m-label { color: #43A047; }
.mcard-c5 { background: rgba(21,101,192,0.12); border-left-color: #1E88E5; }
.mcard-c5 .m-badge { background: #1565C0; } .mcard-c5 .m-label { color: #1E88E5; }
</style>
""", unsafe_allow_html=True)


st.markdown("""
<div class="triage-header">
    <div style="font-size:3rem;line-height:1">🏥</div>
    <div>
        <h1>TriageIA — Sistema de Triaje Manchester</h1>
        <div class="sub">Audio → Whisper → Mistral → Random Forest (Orange) → C1–C5</div>
    </div>
</div>
""", unsafe_allow_html=True)


tab_triaje, tab_historial = st.tabs(["🩺 Nuevo Triaje", "📋 Historial"])


# ─────────────────────────────────────────────────────────────────────────────
# TAB 1 — Nuevo Triaje (LLAMA AL API)
# ─────────────────────────────────────────────────────────────────────────────

with tab_triaje:
    st.markdown("### Analizar nueva entrevista clínica")
    st.markdown("Sube un audio en español. El procesamiento completo se hace en la API.")

    uploaded = st.file_uploader(
        "Audio de la entrevista",
        type=["mp3", "wav", "m4a", "ogg"],
        help="Formatos soportados: MP3, WAV, M4A, OGG",
    )

    if uploaded:
        st.audio(uploaded)
        col_btn, _ = st.columns([1, 3])
        with col_btn:
            lanzar = st.button("🔬 Analizar audio", type="primary", use_container_width=True)
    else:
        lanzar = False

    if lanzar and uploaded:
        tiempos = {}

        # ── PASO 1 — Whisper ───────────────────────────────────────────────
        with st.status("🎤 Transcribiendo audio con Whisper...", expanded=True) as s:
            try:
                files = {"file": (uploaded.name, uploaded.getvalue(),
                                  f"audio/{uploaded.name.rsplit('.',1)[-1]}")}
                r1 = httpx.post(f"{API_URL}/fase3/transcribir", files=files, timeout=180.0)
                r1.raise_for_status()
                r1 = r1.json()
            except Exception as exc:
                s.update(label="✗ Error en transcripción", state="error")
                st.error(f"Error: {exc}")
                st.stop()
            tiempos["transcripcion"] = r1["tiempo"]
            guid = r1["guid"]
            transcripcion = r1["transcripcion"]
            st.write(f"📝 **{transcripcion[:150]}...**" if len(transcripcion) > 150 else f"📝 {transcripcion}")
            s.update(label=f"✓ Transcripción completada ({_fmt_dur(r1['tiempo'])})", state="complete")

        # ── PASO 2 — Mistral ──────────────────────────────────────────────
        with st.status("🧠 Extrayendo entidades clínicas con Mistral...", expanded=True) as s:
            try:
                r2 = httpx.post(
                    f"{API_URL}/fase3/extraer",
                    json={"guid": guid, "texto": transcripcion},
                    timeout=90.0,
                )
                r2.raise_for_status()
                r2 = r2.json()
            except Exception as exc:
                s.update(label="✗ Error en extracción", state="error")
                st.error(f"Error: {exc}")
                st.stop()
            tiempos["llm"] = r2["tiempo"]
            entidades      = r2.get("entidades", [])
            entidades_norm = r2.get("entidades_normalizadas", [])
            categoria      = r2.get("categoria", "RES")
            score_ans      = r2.get("score_ansiedad", 0.0)
            resumen        = r2.get("resumen", "")
            razonamiento   = r2.get("razonamiento", "")
            st.write(f"🔍 **{len(entidades_norm)} entidades** detectadas · categoría **{categoria}** · ansiedad **{score_ans:.2f}**")
            s.update(label=f"✓ Extracción completada ({_fmt_dur(r2['tiempo'])})", state="complete")

        # ── PASO 3 — ML (Orange) ──────────────────────────────────────────
        with st.status("🤖 Prediciendo con Random Forest (Orange)...", expanded=True) as s:
            try:
                r3 = httpx.post(
                    f"{API_URL}/fase3/predecir",
                    json={
                        "guid":                   guid,
                        "entidades_normalizadas": entidades_norm,
                        "categoria":              categoria,
                        "score_ansiedad":         score_ans,
                        "texto":                  transcripcion,
                        "resumen":                resumen,
                        "entidades":              entidades,
                        "razonamiento":           razonamiento,
                    },
                    timeout=30.0,
                )
                r3.raise_for_status()
                r3 = r3.json()
            except Exception as exc:
                s.update(label="✗ Error en predicción ML", state="error")
                st.error(f"Error: {exc}")
                st.stop()
            tiempos["ml"]     = r3["tiempo"]
            tiempos["total"]  = sum(tiempos.values())
            pred_ml           = r3["prediccion_ml"]
            entidad_principal = r3["entidad_principal"]
            st.write(f"🎯 Predicción: **{pred_ml}** (entidad principal: **{entidad_principal}**)")
            s.update(label=f"✓ Predicción ML completada ({_fmt_dur(r3['tiempo'])})", state="complete")

        # Refrescar historial para que aparezca el nuevo caso
        get_historial.clear()

        with st.expander("📄 Ver transcripción completa"):
            st.write(transcripcion)

        # ── RESULTADO ──
        st.divider()
        _manchester_card(pred_ml)

        m1, m2, m3 = st.columns(3)
        m1.metric("Predicción ML",    pred_ml or "—")
        m2.metric("Categoría",        categoria or "—")
        m3.metric("Score ansiedad",   f"{score_ans:.2f}")

        st.divider()

        # Deduplicar para que no aparezca "Dolor_Torácico, Dolor_Torácico, Dolor_Torácico"
        sintomas_unicos = list(dict.fromkeys(entidades))
        entidades_unicas = list(dict.fromkeys(entidades_norm))

        if resumen:
            st.markdown(
                _info_card("📝", "Resumen clínico", resumen),
                unsafe_allow_html=True,
            )
        if razonamiento:
            st.markdown(
                _info_card("💭", "Justificación clínica", razonamiento),
                unsafe_allow_html=True,
            )
        st.markdown(
            _info_card("🔍", "Síntomas detectados por el LLM (texto del paciente)",
                       _tag_list(sintomas_unicos)),
            unsafe_allow_html=True,
        )
        st.markdown(
            _info_card("⭐", "Entidad clínica (input al modelo ML)",
                       f"<span class='tag' style='font-size:1.05rem;font-weight:700;"
                       f"background:#1565C0;color:white;border-color:#0D47A1'>{entidad_principal}</span>"),
            unsafe_allow_html=True,
        )

        # Tiempos por fase
        st.markdown("#### ⏱ Tiempos por fase")
        labels = {
            "transcripcion": "🎤 Transcripción Whisper",
            "llm":           "🧠 Extracción IA (Mistral)",
            "ml":            "🤖 Predicción ML (Orange)",
        }
        cols = st.columns(len(labels))
        for i, (k, label) in enumerate(labels.items()):
            cols[i].metric(label, _fmt_dur(tiempos.get(k)))
        st.success(f"**Tiempo total del análisis: {_fmt_dur(tiempos['total'])}**")
        st.caption(f"Caso: `{guid}` · Procesado por la API")


# ─────────────────────────────────────────────────────────────────────────────
# TAB 2 — Historial
# ─────────────────────────────────────────────────────────────────────────────

with tab_historial:
    st.markdown("### Historial de predicciones realizadas")

    col_f, col_btn = st.columns([4, 1])
    with col_btn:
        if st.button("🔄 Actualizar", use_container_width=True):
            get_historial.clear()
            st.rerun()

    df_hist = get_historial(300)

    if df_hist.empty:
        st.info("No hay predicciones todavía. Sube un audio en la pestaña **Nuevo Triaje**.")
    else:
        with col_f:
            filtro_guid = st.text_input("🔎 Buscar por GUID", placeholder="Ej: PAC2026…")

        df_show = df_hist.copy()
        if filtro_guid.strip():
            df_show = df_show[df_show["guid"].str.contains(filtro_guid.strip(), case=False, na=False)]
        df_show = df_show.reset_index(drop=True)

        df_show["icon"]          = df_show["estado"].map(lambda x: ESTADO_ICON.get(x, "⚪"))
        df_show["fecha"]         = df_show["inicio_solicitud"].apply(_to_madrid)
        df_show["fuente"]        = df_show["origen_motor"].fillna("—")
        df_show["total"]         = df_show["dur_e2e_seg"].apply(_fmt_dur)
        df_show["transcripcion"] = df_show["dur_transcripcion_seg"].apply(_fmt_dur)
        df_show["llm"]           = df_show["dur_llm_seg"].apply(_fmt_dur)
        df_show["ml"]            = df_show["dur_etiquetado_seg"].apply(_fmt_dur)

        st.caption("💡 Haz clic en una fila para ver el detalle abajo.")
        event = st.dataframe(
            df_show[["icon", "guid", "fuente", "estado", "fecha", "total", "transcripcion", "llm", "ml"]].rename(columns={
                "icon":           "",
                "guid":           "GUID",
                "fuente":         "Fuente",
                "estado":         "Estado",
                "fecha":          "Fecha (Madrid)",
                "total":          "Total",
                "transcripcion":  "Whisper",
                "llm":            "LLM",
                "ml":             "ML",
            }),
            hide_index=True,
            use_container_width=True,
            height=420,
            on_select="rerun",
            selection_mode="single-row",
        )
        st.caption(f"Mostrando {len(df_show)} de {len(df_hist)} predicciones")

        if event.selection.rows:
            st.divider()
            idx_sel  = event.selection.rows[0]
            guid_sel = df_show.iloc[idx_sel]["guid"]

            st.markdown(f"#### 🔎 Detalle del caso `{guid_sel}`")
            j = descargar_json(guid_sel)
            if not j:
                st.warning(f"No hay JSON enriquecido en MinIO para `{guid_sel}`.")
            else:
                pred = j.get("prediccion_entrenada", "") or ""
                _manchester_card(pred)

                mc1, mc2, mc3 = st.columns(3)
                mc1.metric("Predicción ML",  pred or "—")
                mc2.metric("Categoría",      j.get("categoria", "—") or "—")
                mc3.metric("Score ansiedad", f"{float(j.get('score_ansiedad', 0) or 0):.2f}")

                # ── Resumen clínico generado por el LLM ──────────────────────
                resumen_j = j.get("resumen") or ""
                if resumen_j:
                    st.markdown(_info_card("📝", "Resumen clínico", resumen_j),
                                unsafe_allow_html=True)

                # ── Justificación clínica del LLM ────────────────────────────
                razonamiento_j = j.get("razonamiento") or ""
                if razonamiento_j:
                    st.markdown(_info_card("💭", "Justificación clínica", razonamiento_j),
                                unsafe_allow_html=True)

                # ── Entidad principal (input al modelo ML) ───────────────────
                ent_ppal = j.get("entidad_principal") or "—"
                st.markdown(
                    _info_card("⭐", "Entidad clínica (input al modelo ML)",
                               f"<span class='tag' style='font-size:1.05rem;font-weight:700;"
                               f"background:#1565C0;color:white;border-color:#0D47A1'>{ent_ppal}</span>"),
                    unsafe_allow_html=True,
                )

                # ── Síntomas crudos + entidades normalizadas ─────────────────
                sintomas_raw = list(dict.fromkeys(j.get("entidades") or []))
                if sintomas_raw:
                    st.markdown(
                        _info_card("🔍", "Síntomas detectados (texto del paciente)",
                                   _tag_list(sintomas_raw)),
                        unsafe_allow_html=True,
                    )
                ents_norm = list(dict.fromkeys(j.get("entidades_normalizadas") or []))
                if ents_norm:
                    st.markdown(
                        _info_card("🧬", "Entidades normalizadas (LLM)",
                                   _tag_list(ents_norm)),
                        unsafe_allow_html=True,
                    )

                # ── Transcripción completa ───────────────────────────────────
                texto_j = j.get("texto") or ""
                if texto_j:
                    with st.expander("📄 Ver transcripción completa"):
                        st.write(texto_j)

                # ── Tiempos por fase ─────────────────────────────────────────
                st.markdown("##### ⏱ Tiempos por fase")
                f = df_hist[df_hist["guid"] == guid_sel]
                if not f.empty:
                    f = f.iloc[0]
                    st.dataframe(
                        pd.DataFrame([
                            {"Fase": "🎤 Transcripción Whisper", "Duración": _fmt_dur(f.get("dur_transcripcion_seg"))},
                            {"Fase": "🧠 Extracción IA (Mistral)", "Duración": _fmt_dur(f.get("dur_llm_seg"))},
                            {"Fase": "🤖 Predicción ML (Orange)",  "Duración": _fmt_dur(f.get("dur_etiquetado_seg"))},
                            {"Fase": "⏱  Total end-to-end",        "Duración": _fmt_dur(f.get("dur_e2e_seg"))},
                        ]),
                        hide_index=True,
                        use_container_width=True,
                    )
        else:
            st.info("Selecciona una fila de la tabla para ver el detalle del caso.")
