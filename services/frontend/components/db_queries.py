"""
Queries a la BD de TriageIA usadas por el frontend Streamlit.
"""

import os

import pandas as pd
import psycopg2
import streamlit as st

DATABASE_URL = os.getenv("DATABASE_URL", "")


def _conn():
    return psycopg2.connect(DATABASE_URL)


@st.cache_data(ttl=15)
def get_historial(limit: int = 300) -> pd.DataFrame:
    """
    Lista de entrevistas/predicciones con sus tiempos por fase.
    Una fila = un caso, ordenadas por más reciente.
    """
    sql = """
        SELECT
            guid_entrevista                                                  AS guid,
            estado,
            motor_workflow                                                   AS origen_motor,
            inicio_solicitud,
            fin_solicitud,
            ROUND(EXTRACT(EPOCH FROM (fin_solicitud - inicio_solicitud))::NUMERIC, 2)
                                                                             AS dur_e2e_seg,
            ROUND(EXTRACT(EPOCH FROM (fin_preprocesamiento - inicio_preprocesamiento))::NUMERIC, 2)
                                                                             AS dur_transcripcion_seg,
            ROUND(EXTRACT(EPOCH FROM (fin_extraccion_entidades - inicio_extraccion_entidades))::NUMERIC, 2)
                                                                             AS dur_llm_seg,
            ROUND(EXTRACT(EPOCH FROM (fin_score - inicio_score))::NUMERIC, 2)
                                                                             AS dur_score_seg,
            ROUND(EXTRACT(EPOCH FROM (fin_entrenamiento - inicio_entrenamiento))::NUMERIC, 2)
                                                                             AS dur_etiquetado_seg
        FROM entrevista
        ORDER BY inicio_solicitud DESC NULLS LAST
        LIMIT %s
    """
    try:
        with _conn() as conn:
            return pd.read_sql(sql, conn, params=(limit,))
    except Exception:
        return pd.DataFrame()
