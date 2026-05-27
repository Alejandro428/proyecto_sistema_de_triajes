# TriageIA — Sistema de Triaje Manchester automatizado

Clasifica entrevistas clínicas (audio) en niveles de prioridad **Manchester C1 – C5** combinando:

- **Apache Airflow** para orquestar el pipeline de datos
- **Mistral medium** (LLM) para extraer entidades clínicas y generar etiquetas Ground Truth
- **Orange Data Mining** para entrenar el clasificador
- **OpenAI Whisper** para transcribir audio en tiempo real
- **FastAPI + Streamlit** como capa de aplicación clínica
- **MinIO + Postgres** como Data Lake + auditoría

---

## Las 3 fases del proyecto

```
┌────────────────────────┐   ┌────────────────────────┐   ┌────────────────────────┐
│  FASE 1                │   │  FASE 2                │   │  FASE 3                │
│  Obtención de datos    │ → │  Modelado clínico      │ → │  Predicción en vivo    │
│  Airflow + Mistral     │   │  Orange Data Mining    │   │  FastAPI + Streamlit   │
└────────────────────────┘   └────────────────────────┘   └────────────────────────┘
```

### Fase 1 · Obtención de datos *(automatizada en Airflow)*

Dos DAGs encadenados a partir del dataset **Fareez et al. (2022)** — 272 entrevistas OSCE en `.info`.

| DAG | Entrada | Proceso | Salida |
|---|---|---|---|
| `dag_ingestion` | 272 archivos `.info` | Parseo y estructura | `conversaciones.csv` (guid · texto · turnos) |
| `dag_llm_enrichment` | `conversaciones.csv` | Llamada a **Mistral medium** caso a caso | 270 JSON enriquecidos + `dataset_entrenamiento.csv` |

**Salida final:** `dataset_entrenamiento.csv` con 5 columnas:
`entidades_normalizadas · n_sintomas · categoria · score_ansiedad · etiqueta`

**Almacenamiento (MinIO):**
```
entrenamiento/
  texto/<guid>.txt
  enriquecidos/<guid>.json
  datasets/conversaciones.csv
  datasets/dataset_entrenamiento.csv
```

### Fase 2 · Modelado clínico *(Orange — manual)*

Pipeline visual en Orange (`docs/triagle.ows`):

```
dataset_entrenamiento.csv  →  Filtrar C1 (clase vacía, 0 ejemplos)
                           →  One-Hot (entidades binarias)
                           →  3 modelos entrenados en paralelo:
                                 ├ Random Forest
                                 ├ Logistic Regression
                                 └ Naive Bayes
                           →  modelo.pkcls  →  ./models/
```

Distribución del Ground Truth generado por Mistral (270 casos):

| Etiqueta | Casos | % |
|---|---:|---:|
| C1 — Emergencia | 0 | 0.0 % |
| C2 — Muy urgente | 69 | 25.6 % |
| C3 — Urgente | 119 | 44.1 % |
| C4 — Menos urgente | 60 | 22.2 % |
| C5 — No urgente | 22 | 8.1 % |

Decisiones clínicas:
- **Descartamos C1** del set de etiquetas: el LLM no clasificó ningún caso del dataset Fareez como emergencia, por lo que con 0 ejemplos el modelo no puede aprenderla. En producción nunca predecirá C1 — limitación asumida del dataset.
- **One-Hot sobre `entidades_normalizadas`** evita multicolinealidad — cada entidad del diccionario clínico es una variable binaria independiente.
- **`score_ansiedad`** llega ya como feature desde Fase 1 (lo genera Mistral) — el modelo lo usa para combatir el **under-triage por sesgo emocional** cuando un paciente con pánico minimiza síntomas reales.

#### Comparativa de modelos (validación cruzada 5-fold)

| Modelo | AUC | CA | F1 | Prec | Recall | MCC |
|---|---:|---:|---:|---:|---:|---:|
| **Random Forest** ★ | **0.907** | **0.748** | **0.746** | **0.758** | **0.748** | **0.644** |
| Logistic Regression | 0.896 | 0.707 | 0.702 | 0.702 | 0.707 | 0.569 |
| Naive Bayes | 0.878 | 0.674 | 0.682 | 0.713 | 0.674 | 0.542 |

→ **Random Forest** gana en las 6 métricas y es el modelo activo en el frontend (`models_random_forest.pkcls`). Los otros dos `.pkcls` se mantienen en `./models/` para poder cambiar el modelo activo desde el selector de la app.

### Fase 3 · Predicción en vivo *(Streamlit + FastAPI)*

```
[audio] → Streamlit → POST /fase3/transcribir → Whisper        ┐
                  → POST /fase3/extraer       → Mistral medium  │  encadenados
                  → POST /fase3/predecir      → Modelo .pkcls   ┘
                                                ↓
                                    Resultado Manchester C1–C5
```

En cada paso, el API persiste:
- En **MinIO**: `predicciones/<guid>/{audio.<ext>, transcripcion.txt, dataset.json}`
- En **Postgres** tabla `entrevista`: timestamps de cada fase + `modelo_usado` + `estado`

El **historial** del frontend reconstruye cada caso desde MinIO. **Grafana** (`localhost:3000`) muestra los tiempos por fase en tiempo real.

---

## Arquitectura de almacenamiento

```
MinIO
├── entrenamiento/       ← Fase 1 (batch)
│   ├── texto/
│   ├── enriquecidos/
│   └── datasets/
├── predicciones/        ← Fase 3 (en vivo)
│   └── <guid>/
│       ├── audio.<ext>
│       ├── transcripcion.txt
│       └── dataset.json
└── modelos/             ← Modelos .pkcls (también en ./models/)

Postgres (BD: triageia)
└── entrevista          ← Registro de auditoría
    · guid_entrevista (PK)
    · url_texto_original
    · inicio_solicitud, fin_solicitud
    · inicio/fin_preprocesamiento    (Whisper)
    · inicio/fin_extraccion_entidades (Mistral)
    · inicio/fin_entrenamiento       (Modelo ML)
    · estado · motor_workflow · modelo_usado
```

---

## Setup

```bash
# 1. Variables de entorno
cp .env.example .env
#   → poner tu MISTRAL_API_KEY

# 2. Levantar todos los servicios
docker compose up -d --build

# 3. Ejecutar Fase 1 (entrenamiento batch)
#    Airflow UI → admin / admin
#    Disparar: dag_ingestion → al terminar dispara dag_llm_enrichment

# 4. Entrenar el modelo (Fase 2)
#    Abrir docs/triagle.ows en Orange
#    Usar data/processed/dataset_entrenamiento.csv como entrada
#    Exportar el modelo a ./models/<nombre>.pkcls

# 5. Usar la aplicación (Fase 3)
#    http://localhost:8501 → subir audio → ver predicción
```

---

## Servicios

| Servicio | URL | Credenciales |
|---|---|---|
| Streamlit (Frontend) | `localhost:8501` | — |
| FastAPI (Backend) | `localhost:8000/docs` | — |
| Airflow | `localhost:8080` | admin / admin |
| MinIO Console | `localhost:9001` | minioadmin / minioadmin |
| Postgres | `localhost:5433` | triageia / triageia |
| Grafana | `localhost:3000` | admin / admin |

---

## Estructura del repositorio

```
proyecto_sistema_de_triajes/
├── dags/                       ← DAGs de Airflow (Fase 1)
│   ├── dag_ingestion.py
│   ├── dag_llm_enrichment.py
│   └── pipeline/               ← módulos compartidos (MinIO, LLM, diccionario)
├── services/
│   ├── api/                    ← FastAPI Fase 3
│   ├── frontend/               ← Streamlit
│   ├── airflow/                ← Dockerfile Airflow
│   ├── postgres/               ← Schema SQL
│   └── grafana/                ← Dashboard provisionado
├── models/                     ← Modelos .pkcls de Orange (Fase 2)
├── data/
│   └── processed/              ← Salidas locales de los DAGs (CSVs)
├── docs/
│   ├── triagle.ows             ← Workflow de Orange
│   └── presentacion.pptx       ← Slides de defensa
└── docker-compose.yml
```
