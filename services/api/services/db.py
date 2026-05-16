import logging
from datetime import datetime

import psycopg2

logger = logging.getLogger(__name__)


class DatabaseService:
    def __init__(self, database_url: str):
        self._url = database_url

    def _connect(self):
        return psycopg2.connect(self._url)

    # ------------------------------------------------------------------ #
    # Lectura                                                              #
    # ------------------------------------------------------------------ #

    def obtener_historial(self, limit: int = 200) -> list[dict]:
        sql = """
            SELECT
                guid_entrevista,
                estado,
                motor_workflow,
                inicio_solicitud,
                fin_solicitud,
                fin_score,
                url_texto_original,
                url_modelo_entrenado
            FROM entrevista
            ORDER BY inicio_solicitud DESC NULLS LAST
            LIMIT %s
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (limit,))
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]

    def obtener_entrevista(self, guid: str) -> dict | None:
        sql = "SELECT * FROM entrevista WHERE guid_entrevista = %s"
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (guid,))
                cols = [d[0] for d in cur.description]
                row  = cur.fetchone()
                return dict(zip(cols, row)) if row else None

    def obtener_stats(self) -> dict:
        sql = """
            SELECT
                COUNT(*)                                                          AS total,
                COUNT(*) FILTER (WHERE estado IN ('MODELO_ENTRENADO', 'PREDICCION_COMPLETADA', 'EVALUACION_COMPLETADA')) AS completados,
                COUNT(*) FILTER (WHERE estado = 'ERROR')                         AS errores,
                COUNT(*) FILTER (WHERE estado = 'DATASET_GENERADO')              AS dataset_generados,
                ROUND(AVG(
                    EXTRACT(EPOCH FROM (fin_score - inicio_extraccion_entidades))
                ) FILTER (WHERE fin_score IS NOT NULL)::NUMERIC, 2)              AS avg_llm_seg,
                ROUND(AVG(
                    EXTRACT(EPOCH FROM (fin_solicitud - inicio_solicitud))
                ) FILTER (WHERE fin_solicitud IS NOT NULL)::NUMERIC, 2)          AS avg_e2e_seg
            FROM entrevista
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                cols = [d[0] for d in cur.description]
                row  = cur.fetchone()
                return dict(zip(cols, row)) if row else {}

    def obtener_distribucion_etiquetas(self) -> list[dict]:
        """Cuenta casos por nivel Manchester directamente desde Postgres."""
        sql = """
            SELECT nivel, COUNT(*) AS casos FROM (
                SELECT
                    CASE
                        WHEN url_modelo_entrenado IS NOT NULL THEN 'MODELO_ENTRENADO'
                        ELSE estado
                    END AS nivel
                FROM entrevista
            ) t
            GROUP BY nivel ORDER BY nivel
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]

    # ------------------------------------------------------------------ #
    # Escritura — predicción Fase 2                                        #
    # ------------------------------------------------------------------ #

    def crear_prediccion(self, guid: str, url_audio: str) -> None:
        now = datetime.now()
        sql = """
            INSERT INTO entrevista
                (guid_entrevista, url_texto_original, inicio_solicitud, motor_workflow, estado)
            VALUES (%s, %s, %s, 'API', 'PREDICIENDO')
            ON CONFLICT (guid_entrevista) DO NOTHING
        """
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, (guid, url_audio, now))
                conn.commit()
            logger.info("Predicción creada: %s", guid)
        except Exception:
            logger.exception("Error creando predicción %s", guid)
            raise

    def actualizar_prediccion(self, guid: str, **kwargs) -> None:
        if not kwargs:
            return
        sets   = ", ".join(f"{k} = %s" for k in kwargs)
        params = list(kwargs.values()) + [guid]
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"UPDATE entrevista SET {sets} WHERE guid_entrevista = %s",
                        params,
                    )
                conn.commit()
        except Exception:
            logger.exception("Error actualizando predicción %s", guid)
            raise
