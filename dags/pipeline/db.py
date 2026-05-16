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
    # Entrevista — lectura                                                 #
    # ------------------------------------------------------------------ #

    def existe_entrevista(self, guid: str) -> bool:
        sql = "SELECT 1 FROM entrevista WHERE guid_entrevista = %s"
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (guid,))
                return cur.fetchone() is not None

    def obtener_estado(self, guid: str) -> str | None:
        sql = "SELECT estado FROM entrevista WHERE guid_entrevista = %s"
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (guid,))
                row = cur.fetchone()
                return row[0] if row else None

    def obtener_pendientes_enriquecimiento(self) -> list[dict]:
        sql = """
            SELECT guid_entrevista, url_texto_original
            FROM entrevista
            WHERE estado = 'INGESTED'
            ORDER BY guid_entrevista
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]

    def obtener_guids_enriquecidos(self) -> list[str]:
        sql = """
            SELECT guid_entrevista
            FROM entrevista
            WHERE estado IN ('SCORE_CALCULADO', 'MODELO_ENTRENADO')
            ORDER BY guid_entrevista
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                return [row[0] for row in cur.fetchall()]

    def obtener_historial(self, limit: int = 200) -> list[dict]:
        sql = """
            SELECT guid_entrevista, estado, motor_workflow,
                   inicio_solicitud, fin_solicitud,
                   url_texto_original, url_modelo_entrenado
            FROM entrevista
            ORDER BY inicio_solicitud DESC
            LIMIT %s
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (limit,))
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]

    def obtener_stats_pipeline(self) -> dict:
        sql = """
            SELECT
                COUNT(*)                                              AS total,
                COUNT(*) FILTER (WHERE estado = 'MODELO_ENTRENADO')  AS completados,
                COUNT(*) FILTER (WHERE estado = 'ERROR')             AS errores,
                AVG(EXTRACT(EPOCH FROM (fin_score - inicio_extraccion_entidades)))
                    FILTER (WHERE fin_score IS NOT NULL)              AS avg_llm_seg,
                AVG(EXTRACT(EPOCH FROM (fin_solicitud - inicio_solicitud)))
                    FILTER (WHERE fin_solicitud IS NOT NULL)          AS avg_e2e_seg
            FROM entrevista
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                cols = [d[0] for d in cur.description]
                row = cur.fetchone()
                return dict(zip(cols, row)) if row else {}

    # ------------------------------------------------------------------ #
    # Entrevista — escritura                                               #
    # ------------------------------------------------------------------ #

    def crear_entrevista(self, guid: str, url_texto: str,
                         motor_workflow: str = "Airflow",
                         estado: str = "INGESTED") -> None:
        now = datetime.now()
        sql = """
            INSERT INTO entrevista
                (guid_entrevista, url_texto_original,
                 inicio_solicitud,
                 motor_workflow, estado)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (guid_entrevista) DO NOTHING
        """
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, (guid, url_texto, now, motor_workflow, estado))
                conn.commit()
            logger.info("Entrevista creada: %s", guid)
        except Exception:
            logger.exception("Error creando entrevista %s", guid)
            raise

    def actualizar_entrevista(self, guid: str, **kwargs) -> None:
        if not kwargs:
            return
        sets   = ", ".join(f"{k} = %s" for k in kwargs)
        params = list(kwargs.values()) + [guid]
        sql    = f"UPDATE entrevista SET {sets} WHERE guid_entrevista = %s"
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
                conn.commit()
        except Exception:
            logger.exception("Error actualizando entrevista %s", guid)
            raise
