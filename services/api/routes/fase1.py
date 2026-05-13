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

    try:
        print("→ Paso 1: parseando archivos .info...")
        print(f"   Buscando .info en: {raw_dir}")
        if not raw_dir.exists():
            raise FileNotFoundError(f"Directorio raw no encontrado: {raw_dir}")

        casos = cargar_dataset(raw_dir)
        if not casos:
            raise ValueError("No se encontraron casos en los archivos .info")
        exportar_csv(casos, conversaciones_csv)

        print(f"→ Paso 2: etiquetando {len(casos)} casos con Mistral...")
        print(f"   conversaciones.csv: {conversaciones_csv}")
        print(f"   master.csv destino: {master_csv}")
        procesar_dataset(llm, db, conversaciones_csv, master_csv)

        print("✓ Pipeline Fase 1 completado.")

    except Exception as e:
        import traceback
        print(f"✗ ERROR en pipeline Fase 1: {e}", flush=True)
        traceback.print_exc()
