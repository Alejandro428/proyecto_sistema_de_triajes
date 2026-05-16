FROM apache/airflow:2.9.2

USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ffmpeg \
 && rm -rf /var/lib/apt/lists/*

USER airflow

# CPU-only torch (~700 MB vs ~2 GB con CUDA) para Whisper en dag_prediction_phase_2
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Pre-descarga del modelo Whisper base (~145 MB) para evitar latencia en primera ejecución
RUN python -c "import whisper; whisper.load_model('base')"
