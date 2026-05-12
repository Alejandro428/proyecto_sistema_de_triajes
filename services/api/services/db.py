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
                (id_caso, categoria, origen, texto_original, resumen_es,
                 entidades, entidades_norm, triage_real, justificacion, score_ansiedad)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s)
            ON CONFLICT (id_caso) DO NOTHING
        """
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, (
                    caso["id_caso"],
                    caso["categoria"],
                    caso["origen"],
                    caso["texto_original"],
                    caso.get("resumen_es"),
                    json.dumps(caso.get("entidades_extraidas", [])),
                    json.dumps(caso.get("entidades_normalizadas", [])),
                    caso.get("triage_real"),
                    caso.get("justificacion"),
                    caso.get("score_ansiedad", 0.0),
                ))
            conn.commit()
        finally:
            conn.close()

    def obtener_casos(self) -> list[dict]:
        sql = """
            SELECT id_caso, categoria, origen, texto_original, resumen_es,
                   entidades, entidades_norm, triage_real, justificacion, score_ansiedad
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
                (texto_audio, entidades, score_ansiedad, prediccion, ground_truth, validacion)
            VALUES (%s, %s::jsonb, %s, %s, %s, %s)
        """
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, (
                    prediccion["texto_audio"],
                    json.dumps(prediccion.get("entidades", [])),
                    prediccion.get("score_ansiedad", 0.0),
                    prediccion["prediccion"],
                    prediccion.get("ground_truth"),
                    prediccion.get("validacion"),
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
