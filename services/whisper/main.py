import os
import tempfile

import whisper
from fastapi import FastAPI, File, UploadFile

app = FastAPI(title="TriageIA - Whisper Service", version="0.1.0")

_model = None


def get_model():
    global _model
    if _model is None:
        _model = whisper.load_model(os.getenv("WHISPER_MODEL", "base"))
    return _model


@app.get("/health")
def health():
    return {"status": "ok", "service": "whisper"}


@app.post("/transcribe")
async def transcribe(file: UploadFile = File(...)):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name
    try:
        result = get_model().transcribe(tmp_path)
        return {"text": result["text"], "language": result.get("language")}
    finally:
        os.unlink(tmp_path)
