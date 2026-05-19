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
