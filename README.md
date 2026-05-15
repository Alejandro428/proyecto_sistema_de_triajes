# TriageIA — Sistema de Triaje Médico con IA

Proyecto del Curso de Especialización IA-BD 25/26.
Herramienta de soporte a la decisión clínica que transforma entrevistas clínicas en una prioridad médica estructurada siguiendo el **Protocolo Manchester (C1–C5)**.

Basado en el corpus de **Fareez et al. (2022)** — 270 entrevistas clínicas simuladas (metodología OSCE), publicado en *Nature Scientific Data*.

> El paper original cita 272 casos. Los archivos `.info` entregados contienen 270 IDs válidos con transcripción parseável; los 2 restantes no tienen datos de audio en el dataset distribuido.

---

## Qué hace este sistema

1. Ingiere transcripciones de entrevistas clínicas desde archivos `.info`
2. Las enriquece con un LLM (Mistral `mistral-large-latest`): extrae síntomas en texto libre, los normaliza a terminología médica estándar, asigna un nivel Manchester y calcula un score de ansiedad — todo en una sola llamada con prompt few-shot
3. Genera `dataset_entrenamiento.csv` y entrena un modelo NLP (TF-IDF + Logistic Regression) sobre las entidades normalizadas
4. Rellena `prediccion_entrenada` en todos los casos con el modelo entrenado
5. Expone un dashboard Streamlit (4 pestañas) y una REST API para nuevas predicciones en tiempo real
6. Registra trazabilidad completa por caso: timestamps por etapa, estado, URLs de artefactos en MinIO

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
                                          ┌─────────────────────────┼──────────────────────┐
                                          │                         │                      │
                                 ┌────────▼────────┐    ┌───────────▼──────────┐           │
                                 │  Streamlit      │    │  FastAPI             │  Fase 2   │
                                 │  Frontend       │    │  REST API            │  (→ DAGs) │
                                 │  :8501          │    │  :8000               │           │
                                 └─────────────────┘    └──────────────────────┘           │
                                 Audio→Whisper→Mistral→ML  /fase3/predict                  │
                                                           /metricas/stats         dag_prediction
                                                                                   dag_evaluation
```

### Servicios Docker

| Servicio | Puerto | Rol |
|---|---|---|
| `postgres` | 5433 | Tracking de estados del workflow (tabla `entrevista`) |
| `minio` | 9000 / 9001 | Almacenamiento de artefactos (UI en :9001) |
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
| `audios/` | Audios subidos desde el frontend |

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
Motor_Workflow              -- 'Airflow' (batch) o 'Streamlit' (tiempo real)
Estado                      -- ver máquina de estados abajo
```

### Timestamps por etapa

| Campo | Rellena | Qué mide |
|---|---|---|
| `Inicio_Solicitud` | dag_ingestion | Cuando el caso entra al sistema |
| `Fin_Solicitud` | dag_model_training | Cuando el pipeline completo termina |
| `Inicio/Fin_Preprocesamiento` | dag_llm_enrichment | Limpieza del texto (~milisegundos) |
| `Inicio_Extraccion_Entidades` | dag_llm_enrichment | Inicio de la llamada a Mistral |
| `Fin_Extraccion_Entidades` | dag_llm_enrichment | Fin de la llamada a Mistral (~15s) |
| `Inicio/Fin_Normalizacion` | dag_llm_enrichment | **Mismo valor que Fin_Extraccion** — Mistral hace extracción + normalización + etiquetado + score en una sola llamada |
| `Inicio/Fin_Etiquetado` | dag_llm_enrichment | Ídem |
| `Inicio/Fin_Score` | dag_llm_enrichment | Ídem |
| `Inicio/Fin_Entrenamiento` | dag_model_training | Duración del fit() del modelo ML |

**Nota sobre E2E** (`Fin_Solicitud - Inicio_Solicitud`): en el pipeline batch, todos los 270 casos comparten el mismo `Fin_Solicitud` (cuando terminó el entrenamiento). Por tanto, el E2E de cada caso en Fase 1 es ~107 minutos (tiempo total del pipeline, no tiempo individual). Para nuevos casos procesados en tiempo real (Fase 3 Streamlit), el E2E es ~20-30 segundos.

### Máquina de estados

```
INGESTED → PROCESANDO → SCORE_CALCULADO → DATASET_GENERADO → MODELO_ENTRENADO
                                                                      ↑
                                                          (Fase 3 Streamlit)
                                                          PREDICIENDO → PREDICCION_COMPLETADA
```

---

## Fases del proyecto

### Fase 1 — Ingeniería de datos y entrenamiento ✅ COMPLETADA

**Tres DAGs en Airflow que se lanzan en cadena automáticamente:**

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
| `prediccion_entrenada` | Predicción del modelo ML (rellena al final de `dag_model_training`) |

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

- **Enfoque NLP**: el modelo trabaja sobre texto (entidades normalizadas unidas como frase). TF-IDF captura la importancia de cada término clínico y sus combinaciones (`ngram_range=(1,2)`), más apropiado que representación binaria de presencia/ausencia.
- **Normalización previa por Mistral**: el LLM convierte "can't breathe", "me ahogo" y "falta de aire" en `"Disnea"`. TF-IDF sobre entidades normalizadas equivale a trabajar en un espacio semántico limpio.
- **Interpretabilidad**: los coeficientes de LogReg muestran qué términos clínicos favorecen cada nivel Manchester (visible en `grafica_importancia.png`).
- **Probabilidades calibradas**: LogReg da probabilidades por clase bien calibradas, útiles para el score de urgencia.
- **Manejo del desequilibrio**: `class_weight='balanced'` pondera automáticamente las clases minoritarias (C1, C2). SMOTE adaptativo se aplica cuando todas las clases tienen ≥ 2 muestras.

**Pipeline de features:**
```
entidades_normalizadas → " ".join() → TfidfVectorizer(ngram_range=(1,2), sublinear_tf=True) ┐
                                                                                              ├── hstack → LogReg
score_ansiedad ──────────────────────────────────────────────────────────────────────────────┘
```

**Métricas actuales** (test 20%, 54 casos):
```
Accuracy:   51.85%
Precision:  49.86% (macro)
Recall:     65.99% (macro)  ← métrica clave en triaje clínico
F1:         46.48% (macro)
```

**Artefacto guardado en MinIO** (`modelos/modelo_triageia.pkl`):
```python
{
    "vectorizador": TfidfVectorizer,   # para transform() en predicción
    "modelo":       LogisticRegression,
    "metricas":     {"accuracy": 0.5185, "precision_macro": ..., "n_casos": 270, ...}
}
```

La limitación principal es que C1 tiene solo 1 caso en el dataset (Mistral asignó muy pocas emergencias), lo que hace imposible aprender esa clase. Se mejorará añadiendo casos de simulación en Fase 2.

---

### Fase 2 — Predicción y evaluación ⏳ PENDIENTE

Dos DAGs nuevos que operan sobre casos **no vistos durante el entrenamiento**:

| DAG | Input | Qué hace | Output |
|---|---|---|---|
| `dag_prediction` | Nuevos archivos `.info` en `data/predict/` | Parsea → Mistral → carga modelo → predice | Nuevas filas en `entrevista` con `prediccion_entrenada` |
| `dag_evaluation` | Casos con etiqueta real + predicción | Calcula accuracy/recall/F1, detecta under-triage | Métricas en MinIO |

Los nuevos casos usarán los mismos campos de la tabla `entrevista`. El `dag_evaluation` compara `etiqueta` (ground truth del LLM) con `prediccion_entrenada` (predicción del modelo ML).

---

### Fase 3 — Frontend y auditoría ✅ COMPLETADA

Dashboard Streamlit con 4 pestañas — http://localhost:8501:

| Pestaña | Función |
|---|---|
| 🩺 Nuevo Triaje | Sube audio → Whisper (`base`) transcribe → Mistral analiza → ML predice → resultado Manchester con colores, métricas de tiempo por fase |
| 📋 Historial | Tabla filtrable de todos los casos (timestamp Madrid, duraciones formateadas), detalle por GUID con tarjetas por sección |
| 📊 Métricas del Pipeline | KPIs: total/completadas/errores, tiempo medio LLM y E2E; gráficas de distribución de estados y latencia LLM; tiempos medios por etapa |
| 🤖 Métricas del Modelo | Accuracy/Recall/F1/F1, gráficas (distribución Manchester, matriz de confusión, top-15 términos clínicos), detección de under-triage del historial |

**Detalles técnicos del frontend:**
- Whisper integrado directamente en el contenedor (no microservicio separado). Modelo `base` descargado en tiempo de build.
- Timestamps mostrados en horario de Madrid (Europe/Madrid), duraciones formateadas como `Xm Ys`.
- CSS adaptativo a modo claro/oscuro usando variables nativas de Streamlit (`var(--background-color)`, `var(--primary-color)`, etc.).
- Tarjetas Manchester con `rgba()` al 12% de opacidad — funcionan en ambos modos sin selectores de tema.

**FastAPI** — http://localhost:8000/docs:

| Endpoint | Descripción |
|---|---|
| `POST /fase3/predict` | Recibe texto, llama a Mistral, predice con ML, guarda en Postgres |
| `GET /fase3/entrevista/{guid}` | Devuelve JSON enriquecido de un caso |
| `GET /metricas/stats` | KPIs del pipeline |
| `GET /metricas/historial` | Últimos N casos con sus timestamps |

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
│   │   ├── db.py               # DatabaseService (crear/actualizar entrevista)
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
│       ├── .streamlit/
│       │   └── config.toml     # primaryColor azul, permite dark/light nativo
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

Distribución de niveles Manchester asignados por Mistral (ground truth del dataset):

| Nivel | Casos | Nota |
|---|---|---|
| C1 | 1 | Insuficiente para aprender esta clase |
| C2 | 30 | |
| C3 | 70 | |
| C4 | 111 | Clase mayoritaria |
| C5 | 58 | |
