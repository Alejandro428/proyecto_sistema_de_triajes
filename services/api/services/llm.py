import anthropic


class LLMService:
    def __init__(self, api_key: str):
        self.client = anthropic.Anthropic(api_key=api_key)

    def procesar_caso(self, texto: str) -> dict:
        # Implementado en Fase 1 - Paso 2 (diseño del prompt)
        pass
