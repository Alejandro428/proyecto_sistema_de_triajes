import httpx

MISTRAL_API_URL = "https://api.mistral.ai/v1/chat/completions"
MODELO = "mistral-large-latest"


class LLMService:
    def __init__(self, api_key: str):
        self.api_key = api_key

    def procesar_caso(self, system_prompt: str, texto: str) -> str:
        response = httpx.post(
            MISTRAL_API_URL,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": MODELO,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": texto},
                ],
            },
            timeout=60.0,
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]
