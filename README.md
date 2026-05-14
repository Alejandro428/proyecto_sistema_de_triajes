# TriageIA — Sistema de Triaje Médico con IA

Proyecto del Curso de Especialización IA-BD 25/26.
Herramienta de soporte a la decisión clínica que transforma entrevistas clínicas en una prioridad médica estructurada siguiendo el **Protocolo Manchester (C1–C5)**.

Basado en el corpus de **Fareez et al. (2022)** — 272 entrevistas clínicas simuladas (metodología OSCE), publicado en *Nature Scientific Data*.

---

## Qué hace este sistema

1. Ingiere transcripciones de entrevistas clínicas desde archivos `.info`
2. Las enriquece con un LLM (Mistral): extrae síntomas, los normaliza a terminología médica estándar, asigna un nivel de urgencia Manchester y calcula un score de ansiedad
3. Genera un dataset etiquetado (`dataset_entrenamiento.csv`) con el que entrena un modelo de Machine Learning
4. Usa ese modelo para predecir el nivel de urgencia de nuevos pacientes (Fase 2)
5. Registra todo el flujo con trazabilidad completa por caso (GUID único por entrevista)

---

## Arquitectura actual

```
data/raw/ (.info)
     │
     ▼
┌──────────────────┐     ┌──────────────────────┐     ┌─────────────────────────┐
│  dag_ingestion   │────▶│  dag_llm_enrichment  │────▶│   dag_model_training    │
│                  │     │                      │     │                         │
│ Parsea .info     │     │ Llama a Mistral       │     │ Lee dataset_entren.csv  │
│ → conversaciones │     │ → JSON por caso       │     │ Entrena RandomForest    │
│   .csv en MinIO  │     │ → dataset_entren.csv  │     │ → modelo.pkl en MinIO   │
│ → Postgres       │     │   en MinIO            │     │ → 3 gráficas en MinIO   │
│                  │     │ → Postgres            │     │ → Postgres              │
└──────────────────┘     └──────────────────────┘     └─────────────────────────┘
  (dispara auto →)          (dispara auto →)
```

### Servicios Docker

| Servicio | Puerto | Rol |
|---|---|---|
| `postgres` | 5432 | Tracking de estados del workflow (tabla Entrevista) |
| `minio` | 9000 / 9001 | Almacenamiento de archivos: textos, JSONs, datasets, modelos, gráficas |
| `airflow-webserver` | 8080 | UI de Airflow — lanzar DAGs y ver logs |
| `airflow-scheduler` | — | Motor que ejecuta los DAGs |
| `api` | 8000 | Endpoint de predicción (Fase 2, pendiente) |

### Buckets MinIO

| Bucket | Contenido |
|---|---|
| `textos/` | Transcripciones originales: `{guid}.txt` |
| `enriquecidos/` | JSON con análisis de Mistral por caso: `{guid}.json` (intermedio, para reanudar si falla) |
| `datasets/` | `conversaciones.csv` y `dataset_entrenamiento.csv` |
| `modelos/` | `modelo_triageia.pkl` + 3 gráficas PNG |

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

## Base de datos — tabla Entrevista

Una única tabla para tracking del workflow. Los datos clínicos viven en MinIO.

```sql
GUID_Entrevista             -- clave primaria (ej: RES1, CAR3, MSK47)
URL_Texto_Original          -- minio://textos/{guid}.txt
URL_Dataset_Generado        -- minio://datasets/dataset_entrenamiento.csv
URL_Modelo_Entrenado        -- minio://modelos/modelo_triageia.pkl
Inicio/Fin_Solicitud        -- timestamps de ingesta
Inicio/Fin_Preprocesamiento
Inicio/Fin_Extraccion_Entidades
Inicio/Fin_Normalizacion
Inicio/Fin_Etiquetado
Inicio/Fin_Score
Inicio/Fin_Entrenamiento
Motor_Workflow              -- siempre "Airflow"
Workflow_Id
Estado                      -- INGESTED → PROCESANDO → SCORE_CALCULADO → DATASET_GENERADO → MODELO_ENTRENADO
```

---

## Fases del proyecto

### Fase 1 — Ingeniería de datos y entrenamiento del modelo (COMPLETADA)

**Objetivo:** Generar el dataset etiquetado y entrenar el modelo ML.

**Tres DAGs en Airflow que se lanzan en cadena:**

| DAG | Input | Output | Estado final en BD |
|---|---|---|---|
| `dag_ingestion` | Archivos `.info` en `data/raw/` | `conversaciones.csv` en MinIO | `INGESTED` |
| `dag_llm_enrichment` | `conversaciones.csv` | `dataset_entrenamiento.csv` en MinIO | `DATASET_GENERADO` |
| `dag_model_training` | `dataset_entrenamiento.csv` | `modelo_triageia.pkl` + 3 gráficas en MinIO | `MODELO_ENTRENADO` |

**Columnas de `conversaciones.csv`:**

| Columna | Descripción |
|---|---|
| `guid` | Identificador único del caso (RES1, CAR3...) |
| `origen` | `Dataset` o `Simulación` |
| `categoria` | Categoría médica: RES / MSK / GAS / CAR |
| `texto` | Transcripción completa de la entrevista |

**Columnas de `dataset_entrenamiento.csv`** (según enunciado):

| Columna | Descripción |
|---|---|
| `guid` | Identificador único del caso |
| `origen` | `Dataset` o `Simulación` |
| `categoria` | Categoría médica |
| `texto` | Transcripción completa |
| `resumen` | Resumen en español generado por Mistral |
| `entidades` | Síntomas tal como aparecen en el texto (lista) |
| `entidades_normalizadas` | Términos clínicos estandarizados (lista) |
| `etiqueta` | Nivel Manchester asignado por Mistral: C1-C5 |
| `razonamiento` | Justificación clínica del nivel asignado |
| `score_ansiedad` | Score de ansiedad 0.0–1.0 |
| `prediccion_entrenada` | Vacío en Fase 1, se rellena en Fase 2 |

**Artefactos generados por `dag_model_training`:**
- `modelo_triageia.pkl` — RandomForest + MultiLabelBinarizer (para usar en Fase 2)
- `grafica_distribucion.png` — Distribución de niveles C1-C5 en el dataset
- `grafica_confusion.png` — Matriz de confusión sobre el 20% de test
- `grafica_importancia.png` — Top 15 términos clínicos más relevantes

**Cómo ejecutar Fase 1:**
```bash
# Arrancar todo (la primera vez o tras cambios de código)
docker-compose down -v
docker-compose up --build

# Esperar ~1 minuto a que Airflow esté listo
# http://localhost:8080  →  usuario: admin  /  contraseña: admin

# En la UI de Airflow:
# → Activar y lanzar dag_ingestion
# → dag_llm_enrichment se lanza automáticamente al terminar (tarda ~10 min por rate limit de Mistral)
# → dag_model_training se lanza automáticamente al terminar dag_llm_enrichment
```

> **Nota:** si dag_llm_enrichment falla a mitad, puedes re-lanzarlo sin perder trabajo: los casos ya procesados tienen un JSON en MinIO y se saltan automáticamente.

---

### Fase 2 — Predicción con el modelo entrenado (PENDIENTE)

**Objetivo:** Usar el modelo entrenado en Fase 1 para predecir el nivel de urgencia de nuevos pacientes.

| DAG | Qué hace |
|---|---|
| `dag_prediction` | Detecta nuevos textos, carga el modelo, genera predicción, guarda en Postgres |
| `dag_evaluation` | Compara predicciones con el nivel real, calcula accuracy/recall/F1 |

---

### Fase 3 — Frontend visual (PENDIENTE)

**Objetivo:** Interfaz de hospital para mostrar el sistema de forma visual.

- Subida de audio o texto de un nuevo paciente
- Predicción en tiempo real con los colores Manchester
- Historial de casos procesados
- Panel de auditoría ética

---

## Estructura del proyecto

```
proyecto_sistema_de_triajes/
├── dags/
│   ├── pipeline/               # Código compartido por los DAGs
│   │   ├── parser.py           # Lee archivos .info y reconstruye conversaciones
│   │   ├── prompts.py          # System prompt para Mistral (few-shot + Manchester)
│   │   ├── llm.py              # Cliente HTTP para la API de Mistral
│   │   ├── db.py               # Operaciones sobre Postgres (tabla Entrevista)
│   │   └── minio_client.py     # Operaciones sobre MinIO (4 buckets)
│   ├── dag_ingestion.py        # Fase 1 - Paso 1: .info → conversaciones.csv
│   ├── dag_llm_enrichment.py   # Fase 1 - Paso 2: Mistral → dataset_entrenamiento.csv
│   └── dag_model_training.py   # Fase 1 - Paso 3: RandomForest + gráficas + modelo.pkl
├── services/
│   ├── api/                    # FastAPI (endpoint predicción Fase 2)
│   └── airflow/                # Dockerfile de Airflow con dependencias Python
├── infra/
│   └── postgres/
│       ├── 01_create_databases.sh   # Crea la BD 'airflow' para metadatos de Airflow
│       └── 02_schema.sql            # Tabla Entrevista del proyecto
├── data/
│   └── raw/
│       ├── medical_train.info  # Dataset Fareez — entrenamiento
│       └── medical_test.info   # Dataset Fareez — test
├── docs/                       # Enunciados del proyecto
├── docker-compose.yml
└── .env                        # Variables de entorno (no versionado)
```

---

## Variables de entorno necesarias (.env)

```
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

272 entrevistas simuladas (metodología OSCE):
- **RES** — 214 casos respiratorios (asma, neumonía, gripe)
- **MSK** — 46 casos musculoesqueléticos (esguinces, lumbago, gota)
- **GAS** — 6 casos gastrointestinales (gastroenteritis, apendicitis)
- **CAR** — 5 casos cardíacos críticos (angina, infarto)
