import json

import psycopg2


class DatabaseService:
    def __init__(self, database_url: str):
        self.database_url = database_url

    def _connect(self):
        return psycopg2.connect(self.database_url)

    # --- Fase 1 ---

    def existe_caso(self, id_caso: str) -> bool:
        sql = "SELECT 1 FROM casos WHERE id_caso = %s"
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, (id_caso,))
                return cur.fetchone() is not None
        finally:
            conn.close()

    def insertar_caso(self, caso: dict) -> None:
        sql = """
            INSERT INTO casos
                (id_caso, categoria, transcripcion, resumen,
                 sintomas_detectados, terminos_clinicos,
                 nivel_urgencia, razonamiento, nivel_ansiedad)
            VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s)
            ON CONFLICT (id_caso) DO NOTHING
        """
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, (
                    caso["id_caso"],
                    caso["categoria"],
                    caso["transcripcion"],
                    caso.get("resumen"),
                    json.dumps(caso.get("sintomas_detectados", [])),
                    json.dumps(caso.get("terminos_clinicos", [])),
                    caso.get("nivel_urgencia"),
                    caso.get("razonamiento"),
                    caso.get("nivel_ansiedad", 0.0),
                ))
            conn.commit()
        finally:
            conn.close()

    def obtener_casos(self) -> list[dict]:
        sql = """
            SELECT id_caso, categoria, transcripcion, resumen,
                   sintomas_detectados, terminos_clinicos,
                   nivel_urgencia, razonamiento, nivel_ansiedad
            FROM casos
            ORDER BY id_caso
        """
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                cols = [desc[0] for desc in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            conn.close()

    # --- Fase 3 ---

    def insertar_prediccion(self, prediccion: dict) -> None:
        sql = """
            INSERT INTO predicciones
                (transcripcion, terminos_clinicos, nivel_ansiedad,
                 nivel_predicho, nivel_urgencia_real, resultado)
            VALUES (%s, %s::jsonb, %s, %s, %s, %s)
        """
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, (
                    prediccion["transcripcion"],
                    json.dumps(prediccion.get("terminos_clinicos", [])),
                    prediccion.get("nivel_ansiedad", 0.0),
                    prediccion["nivel_predicho"],
                    prediccion.get("nivel_urgencia_real"),
                    prediccion.get("resultado"),
                ))
            conn.commit()
        finally:
            conn.close()

    def obtener_predicciones(self) -> list[dict]:
        sql = "SELECT * FROM predicciones ORDER BY timestamp DESC"
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                cols = [desc[0] for desc in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            conn.close()
