from fastapi import FastAPI

app = FastAPI(title="TriageIA - API", version="0.1.0")


@app.get("/health")
def health():
    return {"status": "ok", "service": "api"}


# Phase 1 — NER and normalization endpoints will be added here
# Phase 2 — Classification endpoint will be added here
# Phase 3 — Full triage pipeline endpoint will be added here
