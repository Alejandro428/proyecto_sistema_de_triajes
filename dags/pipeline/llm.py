import logging
import time

import httpx

logger = logging.getLogger(__name__)

MISTRAL_API_URL = "https://api.mistral.ai/v1/chat/completions"
MODELO          = "mistral-medium-latest"


class LLMService:
    def __init__(self, api_key: str):
        self._api_key = api_key

    def procesar_caso(self, system_prompt: str, texto: str) -> tuple[str, float]:
        """Llama a Mistral y devuelve (respuesta_raw, duracion_segundos)."""
        t0 = time.time()
        response = httpx.post(
            MISTRAL_API_URL,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type":  "application/json",
            },
            json={
                "model":       MODELO,
                "temperature": 0.1,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": texto},
                ],
            },
            timeout=60.0,
        )
        duracion = time.time() - t0
        response.raise_for_status()
        raw = response.json()["choices"][0]["message"]["content"]
        logger.debug("LLM respondió en %.2fs", duracion)
        return raw, duracion
