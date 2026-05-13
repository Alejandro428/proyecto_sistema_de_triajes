import json
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

    def crear_entrevista(self, guid: str, origen: str, url_texto: str) -> None:
        now = datetime.now()
        sql = """
            INSERT INTO entrevista
                (guid_entrevista, origen, url_texto_original,
                 inicio_solicitud, fin_solicitud, estado)
            VALUES (%s, %s, %s, %s, %s, 'INGESTED')
            ON CONFLICT (guid_entrevista) DO NOTHING
        """
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, (guid, origen, url_texto, now, now))
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

    def insertar_transcripcion(self, guid: str, transcripcion: str) -> None:
        sql = """
            INSERT INTO casos (guid_entrevista, transcripcion)
            VALUES (%s, %s)
            ON CONFLICT (guid_entrevista) DO NOTHING
        """
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, (guid, transcripcion))
            conn.commit()
        finally:
            conn.close()

    def actualizar_caso_enriquecido(self, caso: dict) -> None:
        sql = """
            UPDATE casos SET
                resumen             = %s,
                sintomas_detectados = %s::jsonb,
                terminos_clinicos   = %s::jsonb,
                nivel_urgencia      = %s,
                razonamiento        = %s,
                nivel_ansiedad      = %s
            WHERE guid_entrevista = %s
        """
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, (
                    caso.get("resumen"),
                    json.dumps(caso.get("sintomas_detectados", [])),
                    json.dumps(caso.get("terminos_clinicos", [])),
                    caso.get("nivel_urgencia"),
                    caso.get("razonamiento"),
                    caso.get("nivel_ansiedad", 0.0),
                    caso["guid_entrevista"],
                ))
            conn.commit()
        finally:
            conn.close()

    def obtener_casos_pendientes(self) -> list[dict]:
        sql = """
            SELECT c.guid_entrevista, c.transcripcion, e.origen
            FROM casos c
            JOIN entrevista e ON c.guid_entrevista = e.guid_entrevista
            WHERE c.nivel_urgencia IS NULL
            ORDER BY c.guid_entrevista
        """
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                cols = [desc[0] for desc in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            conn.close()

    def obtener_todos_enriquecidos(self) -> list[dict]:
        sql = """
            SELECT e.guid_entrevista, e.origen,
                   c.terminos_clinicos, c.nivel_urgencia, c.nivel_ansiedad
            FROM entrevista e
            JOIN casos c ON e.guid_entrevista = c.guid_entrevista
            WHERE c.nivel_urgencia IS NOT NULL
            ORDER BY e.guid_entrevista
        """
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                cols = [desc[0] for desc in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            conn.close()

    def insertar_prediccion(self, prediccion: dict) -> None:
        sql = """
            INSERT INTO predicciones
                (guid_entrevista, nivel_predicho, nivel_urgencia_real, resultado)
            VALUES (%s, %s, %s, %s)
        """
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, (
                    prediccion.get("guid_entrevista"),
                    prediccion["nivel_predicho"],
                    prediccion.get("nivel_urgencia_real"),
                    prediccion.get("resultado"),
                ))
            conn.commit()
        finally:
            conn.close()
