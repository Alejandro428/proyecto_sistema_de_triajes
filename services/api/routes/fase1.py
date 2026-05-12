import os
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks

from config import DATABASE_URL, MISTRAL_API_KEY
from fase1.labeler import procesar_dataset
from fase1.parser import cargar_dataset, exportar_csv
from services.db import DatabaseService
from services.llm import LLMService

router = APIRouter(prefix="/fase1", tags=["Fase 1 - Generación de datos"])

DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))


@router.post("/generate-csv", status_code=202)
def generate_csv(background_tasks: BackgroundTasks):
    """
    Lanza el pipeline completo de Fase 1 en background:
    1. Parsea los .info → conversaciones.csv
    2. Llama a Mistral por cada caso → master.csv + PostgreSQL
    """
    llm = LLMService(MISTRAL_API_KEY)
    db  = DatabaseService(DATABASE_URL)
    background_tasks.add_task(_pipeline, llm, db)
    return {
        "status":  "iniciado",
        "mensaje": "Pipeline Fase 1 en ejecución. Sigue el progreso en los logs del contenedor api.",
    }


def _pipeline(llm: LLMService, db: DatabaseService) -> None:
    raw_dir            = DATA_DIR / "raw"
    conversaciones_csv = DATA_DIR / "processed" / "conversaciones.csv"
    master_csv         = DATA_DIR / "processed" / "master.csv"

    print("→ Paso 1: parseando archivos .info...")
    casos = cargar_dataset(raw_dir)
    exportar_csv(casos, conversaciones_csv)

    print(f"→ Paso 2: etiquetando {len(casos)} casos con Mistral...")
    procesar_dataset(llm, db, conversaciones_csv, master_csv)

    print("✓ Pipeline Fase 1 completado.")
