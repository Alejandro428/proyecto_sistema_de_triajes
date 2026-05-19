from fastapi import FastAPI

from routes.fase3 import router as fase3_router

app = FastAPI(
    title="TriageIA — API",
    version="1.0.0",
    description="API de predicción de triaje Manchester (modelo Random Forest entrenado en Orange).",
)

app.include_router(fase3_router)


@app.get("/health")
def health():
    return {"status": "ok", "service": "api", "version": "1.0.0"}
