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
                              ┌─── FASE 1 (batch entrenamiento) ───┐
data/raw/ (.info)             │                                    │
     │                        ▼                                    │
┌──────────────────┐  ┌──────────────────────┐  ┌─────────────────────────┐
│  dag_ingestion   │─▶│  dag_llm_enrichment  │─▶│   dag_model_training    │
│                  │  │                      │  │                         │
│ .info → CSV      │  │ Mistral × 270        │  │ TF-IDF + LogReg         │
│ → MinIO/Postgres │  │ → dataset_entren.csv │  │ → modelo.pkl en MinIO   │
└──────────────────┘  └──────────────────────┘  └─────────────────────────┘

                              ┌─── FASE 2 (audios nuevos) ─────────┐
data/audios/ (mp3,wav,…)      │                                    │
     │                        ▼                                    │
┌──────────────────────────┐  ┌──────────────────────────┐
│ dag_prediction_phase_2   │─▶│ dag_evaluation           │
│                          │  │                          │
│ Whisper → Mistral → ML   │  │ etiqueta vs prediccion   │
│ → JSON + entrevista row  │  │ → evaluacion_fase2.json  │
└──────────────────────────┘  └──────────────────────────┘

                              ┌─── FASE 3 (tiempo real) ───────────┐
                              │                                    │
                    ┌─────────▼────────┐    ┌──────────────────────▼───┐
                    │  Streamlit       │    │  FastAPI                 │
                    │  :8501           │    │  :8000                   │
                    │ Audio→Whisper→ML │    │ POST /fase3/predict       │
                    └──────────────────┘    └──────────────────────────┘
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
Fase 1 (batch entrenamiento, Motor_Workflow='Airflow'):
  INGESTED → PROCESANDO → SCORE_CALCULADO → DATASET_GENERADO → MODELO_ENTRENADO

Fase 2 (audios nuevos, Motor_Workflow='Airflow_Fase2'):
  PROCESANDO → PREDICCION_COMPLETADA → EVALUACION_COMPLETADA

Fase 3 (tiempo real, Motor_Workflow='Streamlit' o 'API'):
  PREDICIENDO → PREDICCION_COMPLETADA

Cualquier fase:
  ERROR  (con razón en logs de Airflow + columna Estado)
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

### Fase 2 — Predicción y evaluación sobre audios nuevos ✅ COMPLETADA

Dos DAGs nuevos que procesan **audios reales** (no vistos durante el entrenamiento) usando el modelo de Fase 1:

| DAG | Input | Qué hace | Output |
|---|---|---|---|
| `dag_prediction_phase_2` | Audios en `data/audios/` (mp3/wav/m4a/ogg/webm/flac) | Whisper → Mistral (con retry 429) → modelo ML | JSON en MinIO con `prediccion_prueba` + fila en `entrevista` con `Motor_Workflow='Airflow_Fase2'` |
| `dag_evaluation` | Casos `PREDICCION_COMPLETADA` de Fase 2 | Compara `etiqueta` (LLM) vs `prediccion_prueba` (ML), detecta under/over-triage | `evaluacion_fase2.json` + `evaluacion_fase2_confusion.png` en MinIO `modelos/` |

**Cómo ejecutar Fase 2:**
```bash
# 1. Dejar audios en data/audios/ (cualquier formato soportado por Whisper)
cp mi_paciente_001.mp3 data/audios/

# 2. En Airflow UI lanzar dag_prediction_phase_2
#    → procesa todos los audios nuevos
#    → al terminar dispara dag_evaluation automáticamente

# 3. Resultados en MinIO console (http://localhost:9001 → bucket modelos):
#    - evaluacion_fase2.json    (métricas globales + valoración por caso)
#    - evaluacion_fase2_confusion.png
```

**Reanudación**: el GUID se genera de forma determinista a partir del nombre del archivo (`FASE2-<nombre_sanitizado>`), así que re-lanzar el DAG salta los audios ya procesados (vía `existe_entrevista`).

**Valoración por caso** (formato del enunciado):

| Tipo | Cómo se calcula | Score |
|---|---|---|
| CORRECTO | `etiqueta == prediccion_prueba` | 1.0 |
| OVER_TRIAGE | ML predice MÁS urgente que el LLM | 0.5–0.85 (degrada poco) |
| UNDER_TRIAGE | ML predice MENOS urgente que el LLM (peligroso en clínica) | 0.0–0.75 (degrada fuerte) |

El sesgo de penalización está intencionalmente cargado contra el under-triage porque en triaje real es más grave clasificar un caso emergente como rutinario que al revés.

---

## Gestión de errores

| Punto de fallo | Estrategia |
|---|---|
| **LLM Mistral 429 (rate limit)** | Retry con back-off exponencial (15s → 30s → 60s → 120s, 6 intentos) en `dag_llm_enrichment`; 4 intentos en `dag_prediction_phase_2` |
| **LLM Mistral 5xx u otro error** | Caso marcado `Estado='ERROR'` en Postgres, DAG continúa con el siguiente — se puede re-lanzar y `json_existe` salta los ya procesados |
| **JSON malformado del LLM** | `_extraer_json()` intenta 3 estrategias: parseo directo, limpieza de markdown, regex de `{…}`. Si todas fallan → ERROR |
| **Validación de respuesta LLM** | `_validar_datos()` verifica que `triage_real` esté en {C1..C5} y que existan los campos obligatorios |
| **Whisper devuelve transcripción vacía** | El caso queda en ERROR; el audio no se borra para poder reintentar |
| **Modelo ML no disponible en MinIO** | El DAG aborta con error claro; el frontend muestra `"N/A"` como predicción y un warning |
| **Postgres caído** | `crear_entrevista` / `actualizar_entrevista` levantan la excepción → tarea Airflow falla → retry automático (config en `default_args`) |
| **MinIO caído** | Misma propagación; Airflow reintenta la tarea entera |
| **Tarea Airflow zombie** | Configurado en docker-compose: `ZOMBIE_THRESHOLD_SECS=7200` (2h) — necesario porque dag_llm_enrichment tarda ~70 min |
| **Reanudación de pipeline interrumpido** | Cada DAG es idempotente: ingestion verifica con `existe_entrevista`, enrichment con `json_existe` en MinIO, prediction_phase_2 con GUID determinista por filename |

Todos los errores se registran tanto en los logs de Airflow (por tarea) como en la columna `Estado='ERROR'` de la tabla `entrevista`, lo que permite filtrarlos desde el frontend.

---

## Por qué Airflow y no n8n

El enunciado permite elegir entre n8n, Airflow o ambos. Elegimos **solo Airflow** por las siguientes razones:

- **Procesamiento batch pesado**: el enriquecimiento LLM tarda ~70 min sobre 270 casos. Airflow gestiona reintentos por tarea, persistencia de estado y trazabilidad de runs largos mejor que un flujo visual n8n.
- **DAGs versionados en código**: los workflows viven en `dags/*.py`, revisables en git y testables como código Python normal. n8n los expresa en JSON exportado de la UI, peor para diffs y code review.
- **Dependencias entre DAGs**: `dag_ingestion → dag_llm_enrichment → dag_model_training` se encadenan con `TriggerDagRunOperator`. Lo mismo `dag_prediction_phase_2 → dag_evaluation`.
- **Reanudación**: si un DAG cae a mitad, se puede re-lanzar y cada tarea verifica el estado en Postgres/MinIO antes de reprocesar. n8n requiere lógica manual para esto.
- **Casos de tiempo real ya cubiertos** sin n8n: la API FastAPI (`POST /fase3/predict`) y el frontend Streamlit (Tab "Nuevo Triaje") atienden las peticiones síncronas que serían el caso de uso natural de un webhook n8n.

n8n hubiera sido la opción correcta si necesitásemos integrar muchas APIs externas (Slack, Telegram, email) o exponer un webhook configurable por no-developers. Para este sistema, todos los consumidores están dentro de docker-compose y son código.

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
│   ├── dag_model_training.py   # Fase 1 - Paso 3 (TF-IDF + LogReg)
│   ├── dag_prediction_phase_2.py  # Fase 2 - Predicción sobre audios
│   └── dag_evaluation.py       # Fase 2 - Valoración del modelo
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
│   ├── raw/                    # Fase 1 — .info para entrenamiento
│   │   ├── medical_train.info
│   │   └── medical_test.info
│   └── audios/                 # Fase 2 — audios nuevos (mp3, wav, m4a, …)
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
