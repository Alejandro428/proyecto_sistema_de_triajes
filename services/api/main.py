from fastapi import FastAPI

from routes.fase3 import router as fase3_router

app = FastAPI(title="TriageIA - API", version="0.1.0")

app.include_router(fase3_router)


@app.get("/health")
def health():
    return {"status": "ok", "service": "api"}
