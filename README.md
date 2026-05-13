# TriageIA — Sistema de Triaje Médico con IA

Proyecto del Curso de Especialización IA-BD 25/26.
Herramienta de soporte a la decisión clínica que transforma la voz del paciente en una prioridad médica estructurada siguiendo el **Protocolo Manchester (C1–C5)**.

Basado en el corpus de **Fareez et al. (2022)** — 272 entrevistas clínicas simuladas (metodología OSCE), publicado en *Nature Scientific Data*.

---

## Qué hace este sistema

1. Ingiere transcripciones de entrevistas clínicas
2. Las enriquece con un LLM (Mistral): extrae síntomas, los normaliza a terminología médica estándar, asigna un nivel de urgencia Manchester y calcula un score de ansiedad
3. Genera un dataset etiquetado con el que entrena un modelo de Machine Learning
4. Usa ese modelo para predecir el nivel de urgencia de nuevos pacientes
5. Registra todo el flujo con trazabilidad completa por caso

---

## Arquitectura actual

```
data/raw/ (.info)
     │
     ▼
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────────┐
│  dag_ingestion  │────▶│dag_llm_enrichment│────▶│ dag_model_training  │
│  Parsea .info   │     │  Llama a Mistral  │     │ Entrena RandomForest│
│  → Postgres     │     │  → Postgres       │     │ → MinIO (modelo.pkl)│
│  → MinIO        │     │                   │     │ → MinIO (master.csv)│
└─────────────────┘     └──────────────────┘     └─────────────────────┘
        │                        │                          │
        └────────────────────────┴──────────────────────────┘
                                 │
                          ┌──────▼──────┐
                          │  PostgreSQL  │
                          │  entrevista  │  ← tracking de estados
                          │  casos       │  ← datos enriquecidos
                          │  predicciones│  ← Fase 2
                          └─────────────┘
```

### Servicios Docker

| Servicio | Puerto | Rol |
|---|---|---|
| `postgres` | 5432 | Base de datos: tracking de estados, datos enriquecidos, predicciones |
| `minio` | 9000 / 9001 | Almacenamiento de archivos: textos originales, datasets, modelos entrenados |
| `airflow-webserver` | 8080 | UI de Airflow — lanzar DAGs y ver logs |
| `airflow-scheduler` | — | Motor que ejecuta los DAGs |
| `api` | 8000 | Endpoint de predicción (Fase 2, pendiente) |

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

## Fases del proyecto

### Fase 1 — Ingeniería de datos y generación del dataset (EN CURSO)

**Objetivo:** Generar el dataset etiquetado que usará el modelo ML.

**Tres DAGs en Airflow:**

| DAG | Qué hace | Estado en BD al completar |
|---|---|---|
| `dag_ingestion` | Parsea los archivos `.info`, guarda cada entrevista en Postgres y el texto en MinIO | `INGESTED` |
| `dag_llm_enrichment` | Llama a Mistral por cada caso: extrae síntomas, normaliza términos, asigna nivel Manchester (C1-C5) y calcula score de ansiedad | `SCORE_CALCULADO` |
| `dag_model_training` | Lee todos los casos enriquecidos, entrena un RandomForest, guarda el modelo y el master.csv en MinIO | `MODELO_ENTRENADO` |

**Resultado final de Fase 1:** `master.csv` con 272 casos etiquetados y `modelo_triageia.pkl` en MinIO.

**Cómo ejecutar Fase 1:**
```bash
# Arrancar todo
docker-compose up --build

# Entrar a la UI de Airflow
# http://localhost:8080  →  usuario: admin  /  contraseña: admin

# Lanzar los DAGs en orden:
# 1. dag_ingestion
# 2. dag_llm_enrichment   (tarda ~10 min por rate limit de Mistral)
# 3. dag_model_training
```

---

### Fase 2 — Predicción con el modelo entrenado (PENDIENTE)

**Objetivo:** Usar el modelo entrenado en Fase 1 para predecir el nivel de urgencia de nuevos pacientes.

**Dos DAGs planificados:**

| DAG | Qué hace |
|---|---|
| `dag_prediction` | Detecta nuevos textos en MinIO `predict/`, carga el modelo, genera predicción, guarda en Postgres |
| `dag_evaluation` | Compara predicciones con el nivel real (si se dispone de él), calcula accuracy/recall/F1, guarda métricas |

**Input:** archivo `.txt` con la transcripción del nuevo paciente en el bucket `predict/` de MinIO.
**Output:** nivel de urgencia predicho (C1-C5) guardado en la tabla `predicciones` de Postgres.

---

### Fase 3 — Frontend visual (PENDIENTE)

**Objetivo:** Interfaz de hospital para mostrar el sistema de forma visual.

Se añadirá un contenedor Streamlit al `docker-compose.yml` con:
- Subida de audio o texto de un nuevo paciente
- Llamada al endpoint de predicción de la API
- Visualización del resultado con los colores Manchester (rojo/naranja/amarillo/verde/azul)
- Historial de casos procesados con sus niveles de urgencia
- Panel de auditoría ética de casos de under-triage

---

## Diferencias respecto a la versión anterior

### Arquitectura

| Componente | Versión anterior | Versión actual |
|---|---|---|
| Orquestación | Sin orquestador — scripts Python manuales | **Airflow** con DAGs versionados en Python |
| Almacenamiento de archivos | Carpeta `data/` montada en Docker | **MinIO** (compatible S3) con buckets separados |
| Pipeline Fase 1 | Endpoint HTTP `POST /fase1/generate-csv` en FastAPI | DAGs en Airflow, sin necesidad de API |
| Tracking de estados | Sin tracking — solo logs | Tabla `entrevista` en Postgres con timestamps por etapa |
| Trazabilidad | No había | Cada caso tiene un `guid_entrevista` que recorre todo el pipeline |

### Base de datos

| Tabla | Versión anterior | Versión actual |
|---|---|---|
| `casos` | Tabla única con todos los campos | Separada en `entrevista` (tracking) + `casos` (datos) |
| Tracking de workflow | No existía | `entrevista` con 12 columnas de timestamps (inicio/fin por etapa) |
| Nombres de columnas | Técnicos (`triage_real`, `entidades_norm`) | Legibles (`nivel_urgencia`, `terminos_clinicos`) |

### Servicios eliminados

| Servicio | Por qué se eliminó |
|---|---|
| `mlflow` | Reemplazado por MinIO para almacenar artefactos. No era un requisito del enunciado actualizado |
| `whisper` | Se añadirá en Fase 3 cuando se integre la subida de audio |
| `streamlit` | Se añadirá en Fase 3 como frontend visual |

---

## Estructura del proyecto

```
proyecto_sistema_de_triajes/
├── dags/
│   ├── pipeline/               # Código compartido por los DAGs
│   │   ├── parser.py           # Lee archivos .info y reconstruye conversaciones
│   │   ├── prompts.py          # System prompt para Mistral (few-shot + Manchester)
│   │   ├── llm.py              # Cliente HTTP para la API de Mistral
│   │   ├── db.py               # Operaciones sobre Postgres
│   │   └── minio_client.py     # Operaciones sobre MinIO
│   ├── dag_ingestion.py        # Fase 1 - Paso 1
│   ├── dag_llm_enrichment.py   # Fase 1 - Paso 2
│   └── dag_model_training.py   # Fase 1 - Paso 3
├── services/
│   ├── api/                    # FastAPI (endpoint predicción Fase 2)
│   └── airflow/                # Dockerfile de Airflow con dependencias Python
├── infra/
│   └── postgres/
│       ├── 01_create_databases.sh   # Crea la BD 'airflow' para metadatos
│       └── 02_schema.sql            # Tablas del proyecto
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
