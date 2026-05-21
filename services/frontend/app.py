"""
TriageIA — Frontend Streamlit
Pestañas: Nuevo triaje | Historial
"""

import os
from datetime import datetime, timedelta, timezone
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
    "C1": {"label": "EMERGENCIA",    "tiempo": "0 min",   "desc": "Riesgo vital inmediato"},
    "C2": {"label": "MUY URGENTE",   "tiempo": "10 min",  "desc": "Atención máx. 10 minutos"},
    "C3": {"label": "URGENTE",       "tiempo": "60 min",  "desc": "Atención máx. 1 hora"},
    "C4": {"label": "MENOS URGENTE", "tiempo": "120 min", "desc": "Atención máx. 2 horas"},
    "C5": {"label": "NO URGENTE",    "tiempo": "240 min", "desc": "Atención máx. 4 horas"},
}

ESTADO_TXT = {
    "PREDICCION_COMPLETADA": "Completado",
    "PREDICIENDO":           "En proceso",
    "ERROR":                 "Error",
}

ESTADO_ICON = {
    "PREDICCION_COMPLETADA": "🟢",
    "PREDICIENDO":           "🔵",
    "ERROR":                 "🔴",
}

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
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


def _chips(items: list) -> str:
    if not items:
        return '<span class="chip-empty">Sin datos</span>'
    return '<div class="chips">' + "".join(f'<span class="chip">{e}</span>' for e in items) + '</div>'


def _triage_card(nivel: str) -> None:
    if nivel not in MANCHESTER:
        st.markdown(
            '<div class="triage-result tr-na">'
            '<div class="triage-code">—</div>'
            '<div class="triage-body">'
            '<div class="triage-label">SIN PREDICCIÓN</div>'
            '<div class="triage-desc">Modelo ML no disponible.</div>'
            '</div></div>',
            unsafe_allow_html=True,
        )
        return
    cfg = MANCHESTER[nivel]
    st.markdown(f"""
    <div class="triage-result tr-{nivel.lower()}">
        <div class="triage-code">{nivel}</div>
        <div class="triage-body">
            <div class="triage-label">{cfg['label']}</div>
            <div class="triage-desc">{cfg['desc']}</div>
            <div class="triage-time">Atención · ≤ {cfg['tiempo']}</div>
        </div>
    </div>
    """, unsafe_allow_html=True)


def _panel_header(title: str, hint: str = "") -> None:
    h = f'<span class="panel-hint">{hint}</span>' if hint else ""
    st.markdown(
        f'<div class="panel-header"><span class="panel-title">{title}</span>{h}</div>',
        unsafe_allow_html=True,
    )


def _metric(k: str, v: str) -> str:
    return f'<div class="metric-card"><div class="metric-k">{k}</div><div class="metric-v">{v}</div></div>'


def _metric_row(items: list) -> None:
    cards = "".join(_metric(k, v) for k, v in items)
    cols  = len(items)
    st.markdown(
        f'<div class="metric-row" style="grid-template-columns:repeat({cols},1fr)">{cards}</div>',
        unsafe_allow_html=True,
    )


def _field(label: str, html_value: str) -> None:
    st.markdown(
        f'<div class="field"><div class="field-label">{label}</div>{html_value}</div>',
        unsafe_allow_html=True,
    )


def _empty_state(icon: str, title: str, text: str) -> None:
    st.markdown(f"""
    <div class="empty-state">
        <div class="empty-state-icon">{icon}</div>
        <div class="empty-state-title">{title}</div>
        <div class="empty-state-text">{text}</div>
    </div>
    """, unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# PAGE CONFIG
# ─────────────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="TriageIA",
    page_icon="🏥",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
/* ─── Reset & base ─────────────────────────────────────────────────── */
.main .block-container { padding: 0 36px 80px 36px; max-width: 1400px !important; }
header[data-testid="stHeader"] { background: transparent; height: 0; }
footer, #MainMenu { visibility: hidden; }

html, body, [class*="css"] {
    font-family: -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", Roboto, "Helvetica Neue", sans-serif;
    -webkit-font-smoothing: antialiased;
    text-rendering: optimizeLegibility;
}

/* ─── Brand bar ────────────────────────────────────────────────────── */
.brand-bar {
    display: flex; align-items: center; justify-content: space-between;
    padding: 16px 0 14px 0;
    border-bottom: 1px solid rgba(120,130,150,0.16);
    margin-bottom: 22px;
}
.brand { display: flex; align-items: center; gap: 12px; }
.brand-mark {
    width: 30px; height: 30px;
    background: #0F172A;
    border-radius: 7px;
    display: flex; align-items: center; justify-content: center;
    color: white; font-size: 0.85rem;
}
.brand-text { display: flex; align-items: baseline; gap: 10px; }
.brand-name { font-weight: 800; font-size: 1rem; color: var(--text-color); letter-spacing: -0.015em; }
.brand-sub  { font-size: 0.74rem; color: rgba(120,130,150,1); font-weight: 500; }

.sys-status { display: flex; gap: 18px; align-items: center; }
.sys-item {
    display: inline-flex; align-items: center; gap: 7px;
    font-size: 0.76rem;
    color: rgba(120,130,150,1);
    font-weight: 600;
}
.sys-dot { width: 7px; height: 7px; border-radius: 50%; background: #10B981; box-shadow: 0 0 0 3px rgba(16,185,129,0.12); }
.sys-dot.err  { background: #EF4444; box-shadow: 0 0 0 3px rgba(239,68,68,0.12); }
.sys-dot.warn { background: #F59E0B; box-shadow: 0 0 0 3px rgba(245,158,11,0.12); }

/* ─── Tabs (underline only) ────────────────────────────────────────── */
.stTabs [data-baseweb="tab-list"] {
    gap: 0;
    background: transparent;
    padding: 0;
    margin-bottom: 24px;
    border-bottom: 1px solid rgba(120,130,150,0.16);
}
.stTabs [data-baseweb="tab"] {
    padding: 10px 4px 14px 4px;
    margin: 0 22px 0 0;
    border-radius: 0;
    font-weight: 600;
    font-size: 0.86rem;
    color: rgba(120,130,150,1);
    background: transparent !important;
    border: none;
    border-bottom: 2px solid transparent !important;
    margin-bottom: -1px;
    letter-spacing: 0;
}
.stTabs [data-baseweb="tab"]:hover { color: #0F172A; }
.stTabs [aria-selected="true"] {
    color: #0F172A !important;
    border-bottom: 2px solid #0F172A !important;
    box-shadow: none !important;
}

/* ─── Panel ────────────────────────────────────────────────────────── */
.panel-header {
    display: flex; align-items: baseline; justify-content: space-between;
    margin: 4px 0 14px 0;
    padding-bottom: 10px;
    border-bottom: 1px solid rgba(120,130,150,0.14);
}
.panel-title {
    font-size: 0.68rem; font-weight: 800;
    text-transform: uppercase; letter-spacing: 0.1em;
    color: rgba(80,90,110,1);
}
.panel-hint {
    font-size: 0.74rem;
    color: rgba(120,130,150,1);
    font-weight: 500;
}

/* ─── KPI strip ────────────────────────────────────────────────────── */
.kpi-strip {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 10px;
    margin-bottom: 20px;
}
.kpi {
    border: 1px solid rgba(120,130,150,0.18);
    border-radius: 7px;
    padding: 14px 16px;
    background: var(--background-color);
    position: relative;
}
.kpi-k { font-size: 0.66rem; font-weight: 700; letter-spacing: 0.09em; text-transform: uppercase; color: rgba(120,130,150,1); }
.kpi-v {
    font-size: 1.7rem; font-weight: 800;
    color: var(--text-color);
    margin-top: 4px;
    letter-spacing: -0.025em;
    line-height: 1;
    font-variant-numeric: tabular-nums;
}
.kpi-sub { font-size: 0.72rem; color: rgba(120,130,150,0.85); margin-top: 5px; font-weight: 500; }

/* ─── Empty state ──────────────────────────────────────────────────── */
.empty-state {
    border: 1.5px dashed rgba(120,130,150,0.25);
    border-radius: 8px;
    padding: 64px 24px;
    text-align: center;
    background: rgba(120,130,150,0.025);
}
.empty-state-icon { font-size: 2rem; margin-bottom: 14px; opacity: 0.55; }
.empty-state-title { font-weight: 700; font-size: 0.95rem; color: var(--text-color); margin-bottom: 6px; }
.empty-state-text { font-size: 0.82rem; color: rgba(120,130,150,1); }

/* ─── Triage result card ───────────────────────────────────────────── */
.triage-result {
    border-radius: 8px;
    padding: 22px 26px;
    margin: 4px 0 18px 0;
    color: white;
    display: flex; gap: 24px; align-items: center;
    box-shadow: 0 1px 0 rgba(0,0,0,0.04);
}
.triage-code {
    font-size: 3.2rem; font-weight: 900;
    line-height: 0.9;
    padding-right: 24px;
    border-right: 1px solid rgba(255,255,255,0.28);
    letter-spacing: -0.05em;
    font-variant-numeric: tabular-nums;
}
.triage-body { flex: 1; }
.triage-label { font-size: 1.35rem; font-weight: 800; letter-spacing: -0.015em; line-height: 1; }
.triage-desc  { font-size: 0.88rem; opacity: 0.88; margin-top: 6px; font-weight: 500; }
.triage-time  {
    font-size: 0.7rem; opacity: 0.78;
    margin-top: 10px;
    letter-spacing: 0.1em; text-transform: uppercase; font-weight: 800;
}

.tr-c1 { background: #B71C1C; }
.tr-c2 { background: #E65100; }
.tr-c3 { background: #C56C00; }
.tr-c4 { background: #2E7D32; }
.tr-c5 { background: #1565C0; }
.tr-na { background: #475569; }

/* ─── Metric row ───────────────────────────────────────────────────── */
.metric-row { display: grid; gap: 8px; margin: 4px 0 16px 0; }
.metric-card {
    border: 1px solid rgba(120,130,150,0.18);
    border-radius: 6px;
    padding: 12px 16px;
    background: var(--background-color);
}
.metric-k { font-size: 0.65rem; font-weight: 700; letter-spacing: 0.09em; text-transform: uppercase; color: rgba(120,130,150,1); }
.metric-v {
    font-size: 1.35rem; font-weight: 800;
    margin-top: 3px;
    color: var(--text-color);
    letter-spacing: -0.015em;
    font-variant-numeric: tabular-nums;
}

/* ─── Field / chips ────────────────────────────────────────────────── */
.field { margin-bottom: 18px; }
.field-label {
    font-size: 0.66rem; font-weight: 800;
    text-transform: uppercase; letter-spacing: 0.09em;
    color: rgba(80,90,110,1);
    margin-bottom: 8px;
}
.field-value-primary {
    display: inline-block;
    background: #0F172A;
    color: white;
    padding: 7px 14px;
    border-radius: 6px;
    font-weight: 800;
    font-size: 0.9rem;
    letter-spacing: -0.005em;
    font-variant-numeric: tabular-nums;
}
.chips { display: flex; flex-wrap: wrap; gap: 5px; }
.chip {
    background: rgba(120,130,150,0.08);
    border: 1px solid rgba(120,130,150,0.2);
    color: var(--text-color);
    border-radius: 5px;
    padding: 4px 10px;
    font-size: 0.81rem;
    font-weight: 600;
}
.chip-empty {
    color: rgba(120,130,150,0.85);
    font-style: italic;
    font-size: 0.82rem;
}

/* ─── Quote (transcripción) ────────────────────────────────────────── */
.quote {
    background: rgba(120,130,150,0.05);
    border-left: 3px solid #0F172A;
    border-radius: 0 7px 7px 0;
    padding: 14px 18px;
    font-size: 0.91rem;
    line-height: 1.65;
    color: var(--text-color);
    margin: 4px 0 0 0;
}

/* ─── Footer meta (caso/modelo) ────────────────────────────────────── */
.meta-bar {
    display: flex; flex-wrap: wrap; gap: 22px;
    margin-top: 14px;
    padding: 11px 16px;
    background: rgba(120,130,150,0.05);
    border: 1px solid rgba(120,130,150,0.16);
    border-radius: 7px;
    font-size: 0.8rem;
}
.meta-k { color: rgba(120,130,150,1); font-weight: 600; margin-right: 6px; }
.meta-v {
    color: var(--text-color);
    font-weight: 700;
    font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
    font-size: 0.82rem;
}

/* ─── Buttons ──────────────────────────────────────────────────────── */
.stButton > button[kind="primary"] {
    background: #0F172A;
    color: white !important;
    border: none;
    border-radius: 6px;
    font-weight: 700;
    padding: 10px 18px;
    font-size: 0.85rem;
    letter-spacing: 0.01em;
    transition: background 0.12s ease;
}
.stButton > button[kind="primary"]:hover { background: #1E293B; }
.stButton > button {
    border-radius: 6px;
    font-weight: 600;
    font-size: 0.84rem;
}

/* ─── Inputs ───────────────────────────────────────────────────────── */
.stSelectbox label, .stFileUploader label, .stTextInput label {
    font-weight: 800 !important;
    font-size: 0.66rem !important;
    text-transform: uppercase;
    letter-spacing: 0.09em;
    color: rgba(80,90,110,1) !important;
}

/* ─── Status blocks (st.status) ────────────────────────────────────── */
div[data-testid="stStatusWidget"] {
    border: 1px solid rgba(120,130,150,0.18) !important;
    border-radius: 7px !important;
    background: var(--background-color) !important;
    box-shadow: none !important;
}
div[data-testid="stStatusWidget"] summary {
    font-weight: 600 !important;
    font-size: 0.88rem !important;
}

/* ─── Dataframe ─────────────────────────────────────────────────────── */
.stDataFrame { border: 1px solid rgba(120,130,150,0.18); border-radius: 7px; }

/* ─── Misc ─────────────────────────────────────────────────────────── */
hr { border-color: rgba(120,130,150,0.15) !important; margin: 16px 0 !important; }
.mono { font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace; font-size: 0.86rem; }
.muted { color: rgba(120,130,150,1); font-size: 0.8rem; }
</style>
""", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# BRAND BAR
# ─────────────────────────────────────────────────────────────────────────────

try:
    r_modelos = httpx.get(f"{API_URL}/fase3/modelos", timeout=5.0)
    modelos_disponibles = r_modelos.json().get("modelos", [])
    api_ok = True
except Exception:
    modelos_disponibles = []
    api_ok = False

api_dot = "" if api_ok else " err"
n_modelos = len(modelos_disponibles)
modelos_warn = "" if n_modelos else " warn"

st.markdown(f"""
<div class="brand-bar">
    <div class="brand">
        <div class="brand-mark">⊕</div>
        <div class="brand-text">
            <span class="brand-name">TriageIA</span>
            <span class="brand-sub">Sistema de triaje · Protocolo Manchester</span>
        </div>
    </div>
    <div class="sys-status">
        <span class="sys-item"><span class="sys-dot{api_dot}"></span>API</span>
        <span class="sys-item"><span class="sys-dot"></span>Base de datos</span>
        <span class="sys-item"><span class="sys-dot{modelos_warn}"></span>{n_modelos} modelo{'s' if n_modelos != 1 else ''}</span>
    </div>
</div>
""", unsafe_allow_html=True)


tab_triaje, tab_historial = st.tabs(["Nuevo triaje", "Historial"])


# ─────────────────────────────────────────────────────────────────────────────
# TAB 1 — Nuevo triaje (split workspace: input | resultados)
# ─────────────────────────────────────────────────────────────────────────────

with tab_triaje:
    col_input, col_output = st.columns([2, 3], gap="large")

    # ── Columna izquierda: ENTRADA ──────────────────────────────────────
    with col_input:
        _panel_header("Entrada", "Audio + modelo")

        if modelos_disponibles:
            modelo_seleccionado = st.selectbox(
                "Modelo ML",
                modelos_disponibles,
                help="Modelos .pkcls disponibles en /app/models",
                label_visibility="visible",
            )
        else:
            modelo_seleccionado = None
            st.warning("Sin modelos. Coloca un .pkcls en `./models/`.")

        uploaded = st.file_uploader(
            "Audio de la entrevista",
            type=["mp3", "wav", "m4a", "ogg"],
            label_visibility="visible",
        )

        if uploaded:
            st.audio(uploaded)
            lanzar = st.button("Analizar audio", type="primary", use_container_width=True)
        else:
            lanzar = False
            st.markdown('<div class="muted" style="margin-top:8px">Carga un archivo de audio para comenzar.</div>', unsafe_allow_html=True)

    # ── Columna derecha: RESULTADOS ─────────────────────────────────────
    with col_output:
        if not (lanzar and uploaded):
            _panel_header("Resultado", "Esperando análisis")
            _empty_state(
                "◌",
                "Sin análisis en curso",
                "Sube un audio y pulsa “Analizar audio”. Los resultados aparecerán aquí.",
            )
        else:
            tiempos = {}

            _panel_header("Pipeline", "3 fases · Whisper → Mistral → Modelo ML")

            # PASO 1 — Whisper
            with st.status("1 · Transcribiendo audio con Whisper…", expanded=True) as s:
                try:
                    files = {"file": (uploaded.name, uploaded.getvalue(),
                                      f"audio/{uploaded.name.rsplit('.',1)[-1]}")}
                    r1 = httpx.post(f"{API_URL}/fase3/transcribir", files=files, timeout=180.0)
                    r1.raise_for_status()
                    r1 = r1.json()
                except Exception as exc:
                    s.update(label="1 · Error en transcripción", state="error")
                    st.error(f"Error: {exc}")
                    st.stop()
                tiempos["transcripcion"] = r1["tiempo"]
                guid          = r1["guid"]
                transcripcion = r1["transcripcion"]
                s.update(label=f"1 · Transcripción completada · {_fmt_dur(r1['tiempo'])}", state="complete")
                st.markdown(f'<div class="quote">{transcripcion}</div>', unsafe_allow_html=True)

            # PASO 2 — Mistral
            with st.status("2 · Extrayendo entidades clínicas con Mistral…", expanded=True) as s:
                try:
                    r2 = httpx.post(
                        f"{API_URL}/fase3/extraer",
                        json={"guid": guid, "texto": transcripcion},
                        timeout=90.0,
                    )
                    r2.raise_for_status()
                    r2 = r2.json()
                except Exception as exc:
                    s.update(label="2 · Error en extracción", state="error")
                    st.error(f"Error: {exc}")
                    st.stop()
                tiempos["llm"] = r2["tiempo"]
                entidades      = r2.get("entidades", [])
                entidades_norm = r2.get("entidades_normalizadas", [])
                categoria      = r2.get("categoria", "RES")
                score_ans      = r2.get("score_ansiedad", 0.0)
                resumen        = r2.get("resumen", "")
                razonamiento   = r2.get("razonamiento", "")
                s.update(label=f"2 · Extracción completada · {_fmt_dur(r2['tiempo'])}", state="complete")
                _metric_row([
                    ("Síntomas",   str(len(set(entidades)))),
                    ("Entidades",  str(len(set(entidades_norm)))),
                    ("Categoría",  categoria),
                    ("Ansiedad",   f"{score_ans:.2f}"),
                ])

            # PASO 3 — ML
            nombre_modelo = modelo_seleccionado or "más reciente"
            with st.status(f"3 · Prediciendo con {nombre_modelo}…", expanded=True) as s:
                try:
                    r3 = httpx.post(
                        f"{API_URL}/fase3/predecir",
                        json={
                            "guid":                   guid,
                            "entidades_normalizadas": entidades_norm,
                            "categoria":              categoria,
                            "score_ansiedad":         score_ans,
                            "modelo":                 modelo_seleccionado,
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
                    s.update(label="3 · Error en predicción ML", state="error")
                    st.error(f"Error: {exc}")
                    st.stop()
                tiempos["ml"]     = r3["tiempo"]
                tiempos["total"]  = sum(tiempos.values())
                pred_ml           = r3["prediccion_ml"]
                entidad_principal = r3["entidad_principal"]
                modelo_usado      = r3.get("modelo_usado", nombre_modelo)
                s.update(label=f"3 · Predicción completada · {_fmt_dur(r3['tiempo'])}", state="complete")
                _metric_row([
                    ("Predicción",          pred_ml),
                    ("Entidad principal",   entidad_principal),
                ])

            get_historial.clear()

            # ── RESULTADO FINAL ─────────────────────────────────────────
            st.markdown('<div style="height:18px"></div>', unsafe_allow_html=True)
            _panel_header("Triaje final", f"Caso · {guid}")

            _triage_card(pred_ml)

            _metric_row([
                ("Categoría",       categoria or "—"),
                ("Score ansiedad",  f"{score_ans:.2f}"),
                ("Tiempo total",    _fmt_dur(tiempos["total"])),
            ])

            sintomas_unicos  = list(dict.fromkeys(entidades))
            entidades_unicas = list(dict.fromkeys(entidades_norm))

            _field("Entidad principal · input al modelo ML",
                   f'<span class="field-value-primary">{entidad_principal}</span>')

            _field("Síntomas detectados", _chips(sintomas_unicos))

            _field("Entidades normalizadas", _chips(entidades_unicas))

            _panel_header("Tiempos por fase")
            _metric_row([
                ("Whisper",   _fmt_dur(tiempos.get("transcripcion"))),
                ("Mistral",   _fmt_dur(tiempos.get("llm"))),
                ("Modelo ML", _fmt_dur(tiempos.get("ml"))),
            ])

            st.markdown(f"""
            <div class="meta-bar">
                <div><span class="meta-k">Caso</span><span class="meta-v">{guid}</span></div>
                <div><span class="meta-k">Modelo</span><span class="meta-v">{modelo_usado}</span></div>
            </div>
            """, unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# TAB 2 — Historial (KPIs + master/detail)
# ─────────────────────────────────────────────────────────────────────────────

with tab_historial:
    df_hist = get_historial(300)

    # Compat: si el cache trae un DataFrame previo sin la columna nueva
    if not df_hist.empty and "modelo_usado" not in df_hist.columns:
        df_hist["modelo_usado"] = None

    # ── KPI strip ──────────────────────────────────────────────────────
    total       = len(df_hist)
    if total:
        now_utc  = datetime.now(timezone.utc)
        ts       = pd.to_datetime(df_hist["inicio_solicitud"], utc=True, errors="coerce")
        n_hoy    = int((ts.dt.tz_convert(_MADRID).dt.date == datetime.now(_MADRID).date()).sum())
        n_hora   = int((ts >= (now_utc - timedelta(hours=1))).sum())
        n_err    = int((df_hist["estado"] == "ERROR").sum())
    else:
        n_hoy = n_hora = n_err = 0

    st.markdown(f"""
    <div class="kpi-strip">
        <div class="kpi"><div class="kpi-k">Casos totales</div><div class="kpi-v">{total}</div><div class="kpi-sub">en histórico</div></div>
        <div class="kpi"><div class="kpi-k">Hoy</div><div class="kpi-v">{n_hoy}</div><div class="kpi-sub">desde 00:00</div></div>
        <div class="kpi"><div class="kpi-k">Última hora</div><div class="kpi-v">{n_hora}</div><div class="kpi-sub">60 min</div></div>
        <div class="kpi"><div class="kpi-k">Errores</div><div class="kpi-v">{n_err}</div><div class="kpi-sub">estado ERROR</div></div>
    </div>
    """, unsafe_allow_html=True)

    if df_hist.empty:
        _empty_state("◌", "Sin predicciones registradas",
                     "Cuando proceses un audio en “Nuevo triaje” aparecerá aquí.")
    else:
        col_table, col_detail = st.columns([5, 6], gap="large")

        # ── Tabla (master) ───────────────────────────────────────────────
        with col_table:
            _panel_header("Casos", f"{total} en total")

            f_a, f_b = st.columns([3, 1])
            with f_a:
                filtro_guid = st.text_input(
                    "Buscar por GUID",
                    placeholder="PAC2026…",
                    label_visibility="collapsed",
                )
            with f_b:
                if st.button("Actualizar", use_container_width=True):
                    get_historial.clear()
                    st.rerun()

            df_show = df_hist.copy()
            if filtro_guid.strip():
                df_show = df_show[df_show["guid"].str.contains(filtro_guid.strip(), case=False, na=False)]
            df_show = df_show.reset_index(drop=True)

            df_show["  "]       = df_show["estado"].map(lambda x: ESTADO_ICON.get(x, "·"))
            df_show["Estado"]   = df_show["estado"].map(lambda x: ESTADO_TXT.get(x, x))
            df_show["Fecha"]    = df_show["inicio_solicitud"].apply(_to_madrid)
            df_show["Modelo"]   = df_show["modelo_usado"].fillna("—")
            df_show["Duración"] = df_show["dur_e2e_seg"].apply(_fmt_dur)

            # Altura dinámica: 35px por fila + header. Tope para no salirse.
            table_height = min(38 + 35 * max(len(df_show), 1) + 3, 900)

            event = st.dataframe(
                df_show[["  ", "guid", "Estado", "Modelo", "Fecha", "Duración"]].rename(columns={
                    "guid": "GUID",
                }),
                hide_index=True,
                use_container_width=True,
                height=table_height,
                on_select="rerun",
                selection_mode="single-row",
                column_config={
                    "  ":       st.column_config.TextColumn(width=40),
                    "GUID":     st.column_config.TextColumn(width="small"),
                    "Estado":   st.column_config.TextColumn(width=110),
                    "Modelo":   st.column_config.TextColumn(width="medium"),
                    "Fecha":    st.column_config.TextColumn(width=140),
                    "Duración": st.column_config.TextColumn(width=90),
                },
            )

            st.markdown(
                f'<div class="muted" style="margin-top:8px">Mostrando {len(df_show)} de {total}</div>',
                unsafe_allow_html=True,
            )

        # ── Detalle ─────────────────────────────────────────────────────
        with col_detail:
            if not event.selection.rows:
                _panel_header("Detalle", "Selecciona un caso")
                _empty_state("◯", "Sin caso seleccionado",
                             "Haz clic en una fila de la tabla para ver el detalle del triaje.")
            else:
                idx_sel  = event.selection.rows[0]
                guid_sel = df_show.iloc[idx_sel]["guid"]
                modelo_row = df_show.iloc[idx_sel].get("modelo_usado") or "—"

                _panel_header("Detalle", f"Caso · {guid_sel}")

                j = descargar_json(guid_sel)
                if not j:
                    st.warning(f"No hay JSON enriquecido para `{guid_sel}` en MinIO.")
                else:
                    pred     = j.get("prediccion_entrenada", "") or ""
                    modelo_j = j.get("modelo_usado") or modelo_row

                    _triage_card(pred)

                    _metric_row([
                        ("Categoría",       j.get("categoria", "—") or "—"),
                        ("Score ansiedad",  f"{float(j.get('score_ansiedad', 0) or 0):.2f}"),
                        ("Modelo",          modelo_j),
                    ])

                    sintomas_raw = list(dict.fromkeys(j.get("entidades") or []))
                    ents_norm    = list(dict.fromkeys(j.get("entidades_normalizadas") or []))
                    ent_ppal     = j.get("entidad_principal") or "—"

                    _field("Entidad principal · input al modelo ML",
                           f'<span class="field-value-primary">{ent_ppal}</span>')

                    _field("Síntomas detectados", _chips(sintomas_raw))

                    _field("Entidades normalizadas", _chips(ents_norm))

                    texto_j = j.get("texto") or ""
                    if texto_j:
                        _field("Transcripción", f'<div class="quote">{texto_j}</div>')

                    f_row = df_hist[df_hist["guid"] == guid_sel]
                    if not f_row.empty:
                        f_row = f_row.iloc[0]
                        _panel_header("Tiempos por fase")
                        _metric_row([
                            ("Whisper",   _fmt_dur(f_row.get("dur_transcripcion_seg"))),
                            ("Mistral",   _fmt_dur(f_row.get("dur_llm_seg"))),
                            ("Modelo ML", _fmt_dur(f_row.get("dur_etiquetado_seg"))),
                            ("Total",     _fmt_dur(f_row.get("dur_e2e_seg"))),
                        ])

                    st.markdown(f"""
                    <div class="meta-bar">
                        <div><span class="meta-k">Caso</span><span class="meta-v">{guid_sel}</span></div>
                        <div><span class="meta-k">Modelo</span><span class="meta-v">{modelo_j}</span></div>
                    </div>
                    """, unsafe_allow_html=True)
