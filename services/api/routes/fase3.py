from fastapi import APIRouter

router = APIRouter(prefix="/fase3", tags=["Fase 3 - Predicción"])


@router.post("/predict")
def predict():
    # Implementado en Fase 3
    return {"status": "not_implemented"}
