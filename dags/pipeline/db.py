from datetime import datetime

import psycopg2


class DatabaseService:
    def __init__(self, database_url: str):
        self.database_url = database_url

    def _connect(self):
        return psycopg2.connect(self.database_url)

    def existe_entrevista(self, guid: str) -> bool:
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM entrevista WHERE guid_entrevista = %s", (guid,))
                return cur.fetchone() is not None
        finally:
            conn.close()

    def crear_entrevista(self, guid: str, url_texto: str) -> None:
        now = datetime.now()
        sql = """
            INSERT INTO entrevista
                (guid_entrevista, url_texto_original,
                 inicio_solicitud, fin_solicitud,
                 motor_workflow, estado)
            VALUES (%s, %s, %s, %s, 'Airflow', 'INGESTED')
            ON CONFLICT (guid_entrevista) DO NOTHING
        """
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, (guid, url_texto, now, now))
            conn.commit()
        finally:
            conn.close()

    def actualizar_entrevista(self, guid: str, **kwargs) -> None:
        if not kwargs:
            return
        sets = ", ".join(f"{k} = %s" for k in kwargs)
        params = list(kwargs.values()) + [guid]
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE entrevista SET {sets} WHERE guid_entrevista = %s",
                    params,
                )
            conn.commit()
        finally:
            conn.close()

    def obtener_pendientes_enriquecimiento(self) -> list[dict]:
        sql = """
            SELECT guid_entrevista, url_texto_original
            FROM entrevista
            WHERE estado = 'INGESTED'
            ORDER BY guid_entrevista
        """
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                cols = [desc[0] for desc in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            conn.close()

    def obtener_guids_enriquecidos(self) -> list[str]:
        sql = """
            SELECT guid_entrevista
            FROM entrevista
            WHERE estado IN ('SCORE_CALCULADO', 'MODELO_ENTRENADO')
            ORDER BY guid_entrevista
        """
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                return [row[0] for row in cur.fetchall()]
        finally:
            conn.close()
