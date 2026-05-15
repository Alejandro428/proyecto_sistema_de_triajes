# TriageIA — Sistema de Triaje Médico con IA

Proyecto del Curso de Especialización IA-BD 25/26.
Herramienta de soporte a la decisión clínica que transforma entrevistas clínicas en una prioridad médica estructurada siguiendo el **Protocolo Manchester (C1–C5)**.

Basado en el corpus de **Fareez et al. (2022)** — 270 entrevistas clínicas simuladas (metodología OSCE), publicado en *Nature Scientific Data*.

> El paper original cita 272 casos. Los archivos `.info` entregados contienen 270 IDs válidos con transcripción parseável; los 2 restantes no tienen datos de audio en el dataset distribuido.

---

## Qué hace este sistema

1. Ingiere transcripciones de entrevistas clínicas desde archivos `.info`
2. Las enriquece con un LLM (Mistral): extrae síntomas, los normaliza a terminología médica estándar, asigna un nivel de urgencia Manchester y calcula un score de ansiedad
3. Genera un dataset etiquetado (`dataset_entrenamiento.csv`) y entrena un modelo NLP (TF-IDF + Logistic Regression)
4. Usa ese modelo para predecir el nivel de urgencia de nuevos pacientes
5. Registra todo el flujo con trazabilidad completa por caso (GUID único por entrevista)

---

## Arquitectura

```
data/raw/ (.info)
     │
     ▼
┌──────────────────┐     ┌──────────────────────┐     ┌─────────────────────────┐
│  dag_ingestion   │────▶│  dag_llm_enrichment  │────▶│   dag_model_training    │
│                  │     │                      │     │                         │
│ Parsea .info     │     │ Llama a Mistral       │     │ TF-IDF + LogReg         │
│ → conversaciones │     │ → JSON por caso       │     │ → modelo.pkl en MinIO   │
│   .csv en MinIO  │     │ → dataset_entren.csv  │     │ → prediccion_entrenada  │
│ → Postgres       │     │ → Postgres            │     │ → 3 gráficas en MinIO   │
└──────────────────┘     └──────────────────────┘     └─────────────────────────┘
  (dispara auto →)          (dispara auto →)
                                                                    │
                                                         ┌──────────▼──────────┐
                                                         │  Streamlit Frontend │
                                                         │  + FastAPI          │
                                                         └─────────────────────┘
```

### Servicios Docker

| Servicio | Puerto | Rol |
|---|---|---|
| `postgres` | 5433 | Tracking de estados del workflow (tabla `entrevista`) |
| `minio` | 9000 / 9001 | Almacenamiento de artefactos |
| `airflow-webserver` | 8080 | UI de Airflow — lanzar DAGs y ver logs |
| `airflow-scheduler` | — | Motor que ejecuta los DAGs |
| `frontend` | 8501 | Dashboard Streamlit (audio → Whisper → Mistral → ML) |
| `api` | 8000 | REST API de predicción y métricas |

### Buckets MinIO

| Bucket | Contenido |
|---|---|
| `textos/` | Transcripciones originales: `{guid}.txt` |
| `enriquecidos/` | JSON con análisis de Mistral por caso: `{guid}.json` |
| `datasets/` | `conversaciones.csv` y `dataset_entrenamiento.csv` |
| `modelos/` | `modelo_triageia.pkl` + 3 gráficas PNG |
| `audios/` | Audios subidos desde el frontend (Fase 3) |

---

## Protocolo Manchester

| Nivel | Color | Tiempo máximo | Descripción |
|---|---|---|---|
| C1 | Rojo | 0 min | Emergencia — riesgo vital inmediato |
| C2 | Naranja | 10 min | Muy urgente |
| C3 | Amarillo | 60 min | Urgente |
| C4 | Verde | 120 min | Menos urgente |
| C5 | Azul | 240 min | No urgente |

---

## Base de datos — tabla `entrevista`

Una única tabla para tracking del workflow completo. Los datos clínicos viven en MinIO.

```sql
GUID_Entrevista             -- clave primaria (ej: RES0001, CAR0003, MSK0047)
URL_Texto_Original          -- minio://textos/{guid}.txt
URL_Dataset_Generado        -- minio://datasets/dataset_entrenamiento.csv
URL_Modelo_Entrenado        -- minio://modelos/modelo_triageia.pkl
Inicio/Fin_Solicitud        -- timestamps E2E: ingesta → modelo entrenado
Inicio/Fin_Preprocesamiento
Inicio/Fin_Extraccion_Entidades
Inicio/Fin_Normalizacion
Inicio/Fin_Etiquetado
Inicio/Fin_Score
Inicio/Fin_Entrenamiento
Motor_Workflow              -- 'Airflow' (batch) o 'Streamlit' (tiempo real)
Estado                      -- INGESTED → PROCESANDO → SCORE_CALCULADO → DATASET_GENERADO → MODELO_ENTRENADO
```

---

## Fases del proyecto

### Fase 1 — Ingeniería de datos y entrenamiento ✅ COMPLETADA

**Tres DAGs en Airflow que se lanzan en cadena:**

| DAG | Input | Output | Estado final en BD |
|---|---|---|---|
| `dag_ingestion` | Archivos `.info` en `data/raw/` | `conversaciones.csv` en MinIO | `INGESTED` |
| `dag_llm_enrichment` | `conversaciones.csv` | `dataset_entrenamiento.csv` en MinIO | `DATASET_GENERADO` |
| `dag_model_training` | `dataset_entrenamiento.csv` | `modelo_triageia.pkl` + gráficas + `prediccion_entrenada` rellena | `MODELO_ENTRENADO` |

**Columnas de `dataset_entrenamiento.csv`:**

| Columna | Descripción |
|---|---|
| `guid` | Identificador único del caso (RES0001, CAR0003…) |
| `origen` | `Dataset` o `Simulación` |
| `categoria` | Categoría médica: RES / MSK / GAS / CAR / DER / GEN |
| `texto` | Transcripción completa de la entrevista |
| `resumen` | Resumen en español generado por Mistral |
| `entidades` | Síntomas tal como aparecen en el texto (lista JSON) |
| `entidades_normalizadas` | Términos clínicos estandarizados (lista JSON) |
| `etiqueta` | Nivel Manchester asignado por Mistral: C1–C5 (ground truth) |
| `razonamiento` | Justificación clínica del nivel asignado |
| `score_ansiedad` | Score de ansiedad 0.0–1.0 |
| `prediccion_entrenada` | Predicción del modelo ML (se rellena al final de `dag_model_training`) |

**Cómo ejecutar Fase 1:**
```bash
# Arrancar todo
docker-compose up --build -d

# Esperar ~1 minuto a que Airflow esté listo
# http://localhost:8080  →  usuario: admin  /  contraseña: admin

# En la UI de Airflow:
# → Lanzar dag_ingestion
# → dag_llm_enrichment se lanza automáticamente (tarda ~70 min por rate limit de Mistral)
# → dag_model_training se lanza automáticamente al terminar

# Frontend: http://localhost:8501
# API docs: http://localhost:8000/docs
```

> Si `dag_llm_enrichment` falla a mitad, puedes re-lanzarlo sin perder trabajo: los casos ya procesados tienen JSON en MinIO y se saltan automáticamente gracias a `stat_object`.

---

### Modelo de Machine Learning — decisión técnica

Se optó por **TF-IDF + Logistic Regression** en lugar de RandomForest por las siguientes razones:

- **Enfoque NLP**: el modelo trabaja sobre texto (entidades normalizadas unidas como frase). TF-IDF captura la importancia de cada término clínico y sus combinaciones (`ngram_range=(1,2)`), lo que es más apropiado que una representación binaria de presencia/ausencia.
- **Normalización previa por Mistral**: el LLM ya convierte "can't breathe", "me ahogo" y "falta de aire" en `"Disnea"`. TF-IDF sobre entidades normalizadas equivale a trabajar en un espacio semántico limpio.
- **Interpretabilidad**: los coeficientes de LogReg muestran directamente qué términos clínicos favorecen cada nivel Manchester (visible en `grafica_importancia.png`).
- **Probabilidades calibradas**: LogReg da probabilidades por clase bien calibradas, útiles para el score de urgencia.
- **Manejo del desequilibrio**: `class_weight='balanced'` pondera automáticamente las clases minoritarias (C1, C2). SMOTE adaptativo se aplica cuando las clases tienen suficientes muestras.

**Métricas actuales** (test 20%, 54 casos):
```
Accuracy:   51.85%
Precision:  49.86% (macro)
Recall:     65.99% (macro)  ← métrica clave en triaje clínico
F1:         46.48% (macro)
```

La limitación principal es que C1 tiene solo 1 caso en todo el dataset (el LLM asignó muy pocas emergencias), lo que hace imposible aprender esa clase correctamente. Se mejorará añadiendo casos de simulación en Fase 2.

---

### Fase 2 — Predicción y evaluación ⏳ PENDIENTE

| DAG | Qué hace |
|---|---|
| `dag_prediction` | Detecta nuevos textos, carga el modelo, genera predicción, guarda en Postgres |
| `dag_evaluation` | Compara predicciones con el nivel real, calcula accuracy/recall/F1 |

---

### Fase 3 — Frontend y auditoría ✅ COMPLETADA

Dashboard Streamlit con 4 pestañas:

| Pestaña | Función |
|---|---|
| 🩺 Nuevo Triaje | Sube audio → Whisper transcribe → Mistral analiza → ML predice → resultado con colores Manchester |
| 📋 Historial | Tabla filtrable de todos los casos procesados, detalle por GUID |
| 📊 Métricas del Pipeline | KPIs de latencia, throughput, distribución de estados |
| 🤖 Métricas del Modelo | Accuracy/Recall/F1 del modelo, gráficas, detección de under-triage |

Whisper está integrado directamente en el frontend (no como microservicio separado). El modelo `base` se descarga en tiempo de build del contenedor Docker.

---

## Estructura del proyecto

```
proyecto_sistema_de_triajes/
├── dags/
│   ├── pipeline/               # Módulos compartidos por los DAGs
│   │   ├── config.py           # Variables de entorno centralizadas
│   │   ├── parser.py           # Lee archivos .info y reconstruye conversaciones
│   │   ├── prompts.py          # System prompt few-shot para Mistral (Manchester)
│   │   ├── llm.py              # Cliente HTTP Mistral con timing
│   │   ├── db.py               # Operaciones sobre Postgres
│   │   └── minio_client.py     # Operaciones sobre MinIO
│   ├── dag_ingestion.py        # Fase 1 - Paso 1
│   ├── dag_llm_enrichment.py   # Fase 1 - Paso 2
│   └── dag_model_training.py   # Fase 1 - Paso 3 (TF-IDF + LogReg)
├── services/
│   ├── airflow/                # Dockerfile de Airflow con dependencias Python
│   ├── api/                    # FastAPI — predicción y métricas
│   │   ├── routes/
│   │   │   ├── fase3.py        # POST /fase3/predict, GET /fase3/entrevista/{guid}
│   │   │   └── metricas.py     # GET /metricas/stats, GET /metricas/historial
│   │   └── services/
│   │       ├── db.py           # DatabaseService para la API
│   │       └── minio_service.py
│   └── frontend/               # Streamlit + Whisper
│       ├── app.py              # Dashboard 4 pestañas
│       └── components/
│           ├── db_queries.py   # Consultas cacheadas a Postgres
│           └── minio_helpers.py
├── infra/
│   └── postgres/
│       ├── 01_create_databases.sh
│       └── 02_schema.sql
├── data/
│   └── raw/
│       ├── medical_train.info
│       └── medical_test.info
├── docs/
├── docker-compose.yml
└── .env
```

---

## Variables de entorno (.env)

```env
MISTRAL_API_KEY=tu_api_key_aqui
POSTGRES_DB=triageia
POSTGRES_USER=triageia
POSTGRES_PASSWORD=triageia
MINIO_ROOT_USER=minioadmin
MINIO_ROOT_PASSWORD=minioadmin
```

---

## Dataset

Fareez et al. (2022). *A dataset of simulated patient-physician medical interviews.*
Nature Scientific Data. [Paper](https://www.nature.com/articles/s41597-022-01423-1)

270 entrevistas con transcripción válida (de 272 en el paper original):

| Categoría | Casos | Descripción |
|---|---|---|
| RES | 211 | Respiratorio — asma, neumonía, gripe |
| MSK | 46 | Musculoesquelético — esguinces, lumbago, gota |
| GAS | 6 | Gastrointestinal — gastroenteritis, apendicitis |
| CAR | 5 | Cardíaco crítico — angina, infarto |
| DER | 1 | Dermatológico |
| GEN | 1 | General |

Distribución de niveles Manchester asignados por Mistral:

| Nivel | Casos |
|---|---|
| C1 | 1 |
| C2 | 30 |
| C3 | 70 |
| C4 | 111 |
| C5 | 58 |
