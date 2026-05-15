from fastapi import FastAPI

from routes.fase3 import router as fase3_router
from routes.metricas import router as metricas_router

app = FastAPI(
    title="TriageIA — API",
    version="0.2.0",
    description="API de predicción y consulta de métricas del sistema de triaje Manchester.",
)

app.include_router(fase3_router)
app.include_router(metricas_router)


@app.get("/health")
def health():
    return {"status": "ok", "service": "api", "version": "0.2.0"}
