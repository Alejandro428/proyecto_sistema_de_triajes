import os
from datetime import datetime

import pandas as pd
import psycopg2
import streamlit as st

DATABASE_URL = os.getenv("DATABASE_URL", "")


def _conn():
    return psycopg2.connect(DATABASE_URL)


@st.cache_data(ttl=30)
def get_historial(limit: int = 300) -> pd.DataFrame:
    sql = """
        SELECT
            guid_entrevista                                              AS guid,
            estado,
            motor_workflow                                               AS origen_motor,
            inicio_solicitud,
            fin_solicitud,
            ROUND(EXTRACT(EPOCH FROM (fin_solicitud - inicio_solicitud))::NUMERIC, 1)
                                                                         AS dur_e2e_seg,
            ROUND(EXTRACT(EPOCH FROM (fin_score - inicio_extraccion_entidades))::NUMERIC, 1)
                                                                         AS dur_llm_seg,
            url_texto_original,
            url_modelo_entrenado
        FROM entrevista
        ORDER BY inicio_solicitud DESC NULLS LAST
        LIMIT %s
    """
    try:
        with _conn() as conn:
            return pd.read_sql(sql, conn, params=(limit,))
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=30)
def get_stats() -> dict:
    sql = """
        SELECT
            COUNT(*)                                                         AS total,
            COUNT(*) FILTER (WHERE estado = 'MODELO_ENTRENADO')             AS completados,
            COUNT(*) FILTER (WHERE estado = 'ERROR')                        AS errores,
            COUNT(*) FILTER (WHERE estado = 'DATASET_GENERADO')             AS en_dataset,
            ROUND(AVG(EXTRACT(EPOCH FROM (fin_score - inicio_extraccion_entidades)))
                FILTER (WHERE fin_score IS NOT NULL)::NUMERIC, 2)           AS avg_llm_seg,
            ROUND(AVG(EXTRACT(EPOCH FROM (fin_solicitud - inicio_solicitud)))
                FILTER (WHERE fin_solicitud IS NOT NULL)::NUMERIC, 2)       AS avg_e2e_seg,
            MIN(inicio_solicitud)                                            AS primera_ingesta,
            MAX(fin_solicitud)                                               AS ultima_solicitud
        FROM entrevista
    """
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                cols = [d[0] for d in cur.description]
                row  = cur.fetchone()
                return dict(zip(cols, row)) if row else {}
    except Exception:
        return {}


@st.cache_data(ttl=60)
def get_pipeline_timing() -> pd.DataFrame:
    """Tiempos por etapa de cada entrevista completada."""
    sql = """
        SELECT
            guid_entrevista                                                          AS guid,
            ROUND(EXTRACT(EPOCH FROM (fin_preprocesamiento   - inicio_preprocesamiento))::NUMERIC, 2)   AS prep_seg,
            ROUND(EXTRACT(EPOCH FROM (fin_extraccion_entidades - inicio_extraccion_entidades))::NUMERIC, 2) AS llm_seg,
            ROUND(EXTRACT(EPOCH FROM (fin_entrenamiento      - inicio_entrenamiento))::NUMERIC, 2)      AS train_seg
        FROM entrevista
        WHERE fin_score IS NOT NULL
        ORDER BY inicio_solicitud DESC
        LIMIT 100
    """
    try:
        with _conn() as conn:
            return pd.read_sql(sql, conn)
    except Exception:
        return pd.DataFrame()
