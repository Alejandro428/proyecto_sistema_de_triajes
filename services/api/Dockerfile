FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

# ffmpeg es necesario para Whisper (decodificar audios)
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Torch CPU-only (~700 MB en lugar de ~2 GB con CUDA — sin GPU disponible)
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-descargar modelo Whisper (~140 MB) en tiempo de build para no descargarlo
# en la primera petición del usuario
RUN python -c "import whisper; whisper.load_model('base')"

COPY . .

EXPOSE 8000
# Sin --reload: producción más estable, no recarga el modelo Whisper en RAM
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
