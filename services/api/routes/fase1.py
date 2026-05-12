from fastapi import APIRouter

router = APIRouter(prefix="/fase1", tags=["Fase 1 - Generación de datos"])


@router.post("/generate-csv")
def generate_csv():
    # Implementado en Fase 1 - Paso 3 (procesado con LLM)
    return {"status": "not_implemented"}
