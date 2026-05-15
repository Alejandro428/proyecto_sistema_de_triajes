from fastapi import APIRouter, Depends
from services.db import DatabaseService
from config import DATABASE_URL

router = APIRouter(prefix="/metricas", tags=["Métricas del pipeline"])


def get_db() -> DatabaseService:
    return DatabaseService(DATABASE_URL)


@router.get("/stats")
def stats_pipeline(db: DatabaseService = Depends(get_db)):
    """Resumen global: totales, errores, tiempos medios."""
    return db.obtener_stats()


@router.get("/historial")
def historial(limit: int = 100, db: DatabaseService = Depends(get_db)):
    """Últimas N entrevistas con su estado y timestamps."""
    rows = db.obtener_historial(limit=limit)
    for r in rows:
        for k, v in r.items():
            if hasattr(v, "isoformat"):
                r[k] = v.isoformat()
    return rows
