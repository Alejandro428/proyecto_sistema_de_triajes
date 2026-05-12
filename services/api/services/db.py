import psycopg2


class DatabaseService:
    def __init__(self, database_url: str):
        self.database_url = database_url

    def _connect(self):
        return psycopg2.connect(self.database_url)

    # --- Fase 1 ---

    def insertar_caso(self, caso: dict) -> None:
        # Implementado en Fase 1 - Paso 4
        pass

    def obtener_casos(self) -> list[dict]:
        # Implementado en Fase 1 - Paso 4
        pass

    # --- Fase 3 ---

    def insertar_prediccion(self, prediccion: dict) -> None:
        # Implementado en Fase 3
        pass

    def obtener_predicciones(self) -> list[dict]:
        # Implementado en Fase 3
        pass
