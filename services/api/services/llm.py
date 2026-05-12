from mistralai import Mistral

MODELO = "mistral-large-latest"


class LLMService:
    def __init__(self, api_key: str):
        self.client = Mistral(api_key=api_key)

    def procesar_caso(self, system_prompt: str, texto: str) -> str:
        # Implementado en Fase 1 - Paso 2 (diseño del prompt)
        response = self.client.chat.complete(
            model=MODELO,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": texto},
            ],
        )
        return response.choices[0].message.content
