# TriageIA — Sistema de Triaje Manchester automatizado

Sistema que clasifica entrevistas clínicas en niveles de prioridad **Manchester (C1-C5)** combinando:

- **Whisper** para transcribir audio
- **Mistral** (LLM) para extraer entidades clínicas
- Un **clasificador ML** entrenado en **Orange Data Mining** para predecir el nivel

---

## Tabla de contenidos

1. [Arquitectura](#arquitectura)
2. [Flujo end-to-end](#flujo-end-to-end)
3. [Stack tecnológico](#stack-tecnológico)
4. [Estructura del repositorio](#estructura-del-repositorio)
5. [Setup inicial](#setup-inicial)
6. [Uso del sistema](#uso-del-sistema)
7. [Workflow de Orange Data Mining](#workflow-de-orange-data-mining)
8. [Diccionario clínico](#diccionario-clínico)
9. [Esquema de la base de datos](#esquema-de-la-base-de-datos)
10. [Servicios y puertos](#servicios-y-puertos)
11. [Variables de entorno](#variables-de-entorno)

---

## Arquitectura

```
┌─────────────────────────────────────────────────────────────────────┐
│                        FASE 1 — DATASET (Airflow)                   │
│                                                                     │
│  .info files (Fareez OSCE)                                          │
│         │                                                           │
│         ▼                                                           │
│  [dag_ingestion]  parser → conversaciones.csv (MinIO + disco)       │
│         │                                                           │
│         ▼  (trigger automático)                                     │
│  [dag_llm_enrichment]                                               │
│      Mistral extrae entidades por cada conversación                 │
│      diccionario_clinico normaliza a 10 entidades estándar               │
│      → dataset_entrenamiento.csv                                    │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│         FASE 2 — ENTRENAMIENTO (Orange Data Mining, manual)         │
│                                                                     │
│  dataset_entrenamiento.csv                                          │
│         │                                                           │
│         ▼                                                           │
│   File → BoW Binary → Continuize → Modelo → Save Model             │
│   Test & Score (CV 5-fold) + Confusion Matrix para validar          │
│                                                                     │
│   Salida: models/<nombre_modelo>.pkcls                              │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     FASE 3 — PREDICCIÓN (Streamlit)                 │
│                                                                     │
│  Usuario sube audio                                                 │
│         │                                                           │
│   1. Whisper          → transcripción                              │
│   2. Mistral (LLM)    → 2-5 entidades, categoría, score_ansiedad   │
│                         (NO predice triaje)                         │
│   3. diccionario      → entidades normalizadas + n_sintomas         │
│   4. Modelo ML (Orange) → predicción C1-C5                          │
│                                                                     │
│   Tiempos por fase → PostgreSQL                                     │
│   Resultado JSON   → MinIO                                          │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Flujo end-to-end

### Fase 1 — Construcción del dataset (Airflow)

1. **`dag_ingestion`** lee los ficheros `.info` del dataset Fareez OSCE en `data/raw/`:
   - Parsea cada línea (regex sobre `audio/CAR0001.mp3[start,end]\ttexto`)
   - Agrupa por caso y ordena por timestamp
   - Sube cada texto a MinIO (`textos/{guid}.txt`)
   - Genera **`conversaciones.csv`** (270 filas) en `data/processed/` y MinIO
2. **`dag_llm_enrichment`** (se lanza automáticamente al terminar el anterior):
   - Por cada conversación llama a la API de Mistral con el prompt clínico
   - Mistral devuelve `entidades_extraidas`, `entidades_normalizadas`, `triage_real`, `score_ansiedad`, `resumen_es`, `justificacion`
   - Aplica el **diccionario clínico** (`pipeline/diccionario_clinico.py`) que normaliza las etiquetas crudas del LLM a las **10 entidades estándar** del vocabulario (hasta 5 por caso, ordenadas por gravedad)
   - Cada JSON enriquecido se guarda en MinIO (`enriquecidos/{guid}.json`) — es idempotente, si ya existe se salta
   - Genera **`dataset_entrenamiento.csv`** con 5 columnas: `entidades_normalizadas` (texto, espacio-separado), `n_sintomas`, `categoria`, `score_ansiedad`, `etiqueta`

> Los DAGs **no escriben en Postgres**: los datos del entrenamiento solo viven
> en MinIO y en `data/processed/`. La tabla `entrevista` se reserva
> exclusivamente para las predicciones en tiempo real de Fase 3 (audios
> subidos desde el frontend o llamadas REST a la API).

### Fase 2 — Entrenamiento (Orange Data Mining)

El entrenamiento se hace **fuera de Docker**, en Orange Desktop, porque:
- Permite comparar visualmente varios modelos
- Genera matrices de confusión, ROC, métricas, etc.
- El profesor puede ver el workflow visual

El modelo resultante (`.pkcls`) se guarda en **`./models/`** (carpeta en la raíz del proyecto). Docker monta esa carpeta como `/app/models` dentro del contenedor de la **API** (ver `docker-compose.yml`). El frontend muestra un **selectbox** con todos los `.pkcls` disponibles — puedes tener varios modelos a la vez y elegir cuál usar sin reiniciar nada. El frontend NO carga el modelo: solo llama al endpoint `/fase3/predecir`.

### Fase 3 — Predicción en producción (Streamlit)

El frontend Streamlit tiene **2 pestañas**:

**🩺 Nuevo Triaje**: subes un audio → ves la predicción ML + tiempos por fase.
**📋 Historial**: tabla con todas las predicciones + detalle al hacer clic.

El flujo interno:
1. **Whisper** transcribe el audio (modelo `base`, español)
2. **Mistral** extrae 2-5 entidades + categoría + score_ansiedad (NO clasifica)
3. Diccionario clínico normaliza las entidades y calcula `n_sintomas`
4. **Modelo ML (Orange)** predice C1-C5 (multi-hot por entidad + n_sintomas + categoría + score)
5. Cada fase queda registrada con timestamps en PostgreSQL

---

## Stack tecnológico

| Capa | Tecnología | Función |
|---|---|---|
| Orquestación | **Apache Airflow 2.9** | DAGs de ingestión y enrichment |
| Backend API | **FastAPI** (Python 3.11) | Endpoints `/fase3/predecir`, `/fase3/modelos` |
| Frontend | **Streamlit** | UI con 2 pestañas |
| BD | **PostgreSQL 16** | Tabla `entrevista` con timestamps por fase |
| Object storage | **MinIO** (S3-compatible) | audios, textos, JSONs enriquecidos, datasets |
| Transcripción | **OpenAI Whisper** (`base`) | Audio → texto en español |
| Extracción NLP | **Mistral** (`mistral-medium-latest`) | Texto → entidades clínicas |
| Modelo ML | **Modelo Orange** (RF, Logístico, SVM…) entrenado en **Orange Data Mining** | Predicción C1-C5 |
| Contenedores | **Docker Compose** | Orquestación local |

---

## Estructura del repositorio

```
proyecto_sistema_de_triajes/
├── README.md
├── docker-compose.yml
├── .env.example                       ← copia a .env y rellena MISTRAL_API_KEY
│
├── dags/                              ← DAGs de Airflow
│   ├── dag_ingestion.py
│   ├── dag_llm_enrichment.py
│   └── pipeline/
│       ├── config.py
│       ├── diccionario_clinico.py     ← 10 entidades estándar + mapeo
│       ├── llm.py                      ← cliente Mistral
│       ├── minio_client.py
│       ├── parser.py                   ← parser de .info
│       └── prompts.py                  ← SYSTEM_PROMPT para Fase 1
│
├── data/
│   ├── raw/                            ← medical_train.info, medical_test.info
│   └── processed/
│       ├── conversaciones.csv          ← 270 entrevistas ordenadas
│       ├── dataset_entrenamiento.csv   ← features para Orange
│       └── analisis_entidades.txt      ← stats del diccionario
│
├── models/                            ← aquí se guarda el .pkcls tras entrenar en Orange
│
└── services/
    ├── airflow/                        ← Dockerfile Airflow
    ├── api/                            ← FastAPI
    │   ├── main.py
    │   ├── config.py
    │   ├── routes/fase3.py
    │   └── services/{db.py, minio_service.py}
    ├── frontend/                       ← Streamlit
    │   ├── app.py
    │   └── components/
    │       ├── db_queries.py
    │       └── minio_helpers.py
    ├── postgres/                       ← scripts init BD
    │   ├── 01_create_databases.sql     ← crea bases triageia + airflow
    │   └── 02_schema.sql               ← tabla entrevista
    └── grafana/                        ← dashboards + datasource
        ├── dashboards/triageia.json
        └── provisioning/{dashboards,datasources}/
```

---

## Setup inicial

### 1. Clonar el repo

```bash
git clone <url>
cd proyecto_sistema_de_triajes
```

### 2. Configurar variables de entorno

```bash
cp .env.example .env
```

Rellenar `MISTRAL_API_KEY` con tu clave (gratis en https://console.mistral.ai).

### 3. Levantar todo

```bash
docker compose up -d --build
```

Esto arranca: PostgreSQL · MinIO · Airflow (init + webserver + scheduler) · API · Frontend.

### 4. Acceder a las interfaces

| Servicio | URL | Credenciales |
|---|---|---|
| Frontend Streamlit | http://localhost:8501 | — |
| API docs (Swagger) | http://localhost:8000/docs | — |
| Airflow UI | http://localhost:8080 | admin / admin |
| MinIO Console | http://localhost:9001 | minioadmin / minioadmin |
| PostgreSQL | localhost:**5433** | triageia / triageia |

---

## Uso del sistema

### A) Generar el dataset (primera vez)

1. Coloca `medical_train.info` y `medical_test.info` en `data/raw/`
2. En Airflow UI lanza el DAG `dag_ingestion` (esto disparará `dag_llm_enrichment` automáticamente al terminar)
3. Espera ~5-10 minutos (Mistral procesa 270 conversaciones)
4. Comprueba que aparecen los CSV en `data/processed/`:
   - `conversaciones.csv`
   - `dataset_entrenamiento.csv`

### B) Entrenar el modelo en Orange Data Mining

1. Instalar [Orange Data Mining](https://orangedatamining.com/)
2. Abrir Orange y montar el workflow:
   - **File** → cargar `data/processed/dataset_entrenamiento.csv`
   - En la tabla de columnas marcar `etiqueta` como **target** y `entidades_normalizadas` como **text** (string)
   - **Corpus** → convierte `entidades_normalizadas` en corpus de texto
   - **Bag of Words Binary** → genera una feature binaria por cada entidad clínica (multi-hot)
   - **Continuize** → One-hot encoding para `categoria` (deja `n_sintomas` y `score_ansiedad` como numéricas)
   - **Clasificador** (p.ej. Random Forest con 200 árboles y Balance class distribution ✓, o Logístico, SVM, kNN…)
   - **Test & Score** → Cross validation 5 folds (Stratified OFF si hay clases con <5 muestras)
   - **Confusion Matrix** y **ROC Analysis** para validar
   - **Save Model** → guardar el `.pkcls` en la carpeta **`./models/`** de la raíz del proyecto. El nombre del archivo es libre; el frontend lo mostrará en el selector de modelos.
3. Guardar el workflow como `docs/triagle.ows` por si hay que reentrenar.

> **¿Dónde se guarda el modelo y cómo lo ve la API?**
>
> - **En tu PC (host):** la carpeta es **`./models/`** en la raíz del repo
>   (junto a `docker-compose.yml`). NO existe ninguna carpeta `models/`
>   dentro de `services/api/`; el contenedor la "ve" porque Docker hace
>   un *bind mount*.
> - **Dentro del contenedor de la API:** aparece como `/app/models` gracias
>   a esta línea de `docker-compose.yml`:
>   ```yaml
>   volumes:
>     - ./models:/app/models
>   ```
> - **Selector de modelos:** el frontend muestra un desplegable con todos los
>   `.pkcls` de `./models/`. Basta con guardar un nuevo archivo y aparecerá
>   automáticamente sin reiniciar nada.
> - Puedes mantener varios `.pkcls` a la vez para comparar modelos.

### C) Hacer predicciones

1. Abrir http://localhost:8501
2. Pestaña **🩺 Nuevo Triaje**
3. Seleccionar el modelo en el desplegable **🤖 Modelo ML**
4. Subir un audio en español (MP3, WAV, M4A, OGG)
5. Pulsar **🔬 Analizar audio**
6. Ver el banner Manchester con la predicción + tiempos por fase

### D) Consultar el historial

Pestaña **📋 Historial** → tabla con todas las predicciones (con filtro por GUID) + detalle al seleccionar un caso (incluye tabla de tiempos por fase).

---

## Workflow de Orange Data Mining

```
┌──────┐  ┌───────────┐  ┌────────────┐    ┌──────────────┐    ┌───────────┐
│ File ├─▶│ BoW Binary├─▶│ Continuize ├──┬▶│  Test&Score  │◀───┤  Modelo   │
└──────┘  └───────────┘  └────────────┘  │ └──────┬───────┘    └─────┬─────┘
                                          │        │                  │
                                          │        ▼                  │
                                          │ ┌──────────────┐          │
                                          │ │Confusion Mtx │          │
                                          │ └──────────────┘          │
                                          │                           ▼
                                          │                   ┌──────────────┐
                                          └──────────────────▶│  Save Model  │
                                                              └──────────────┘
```

**Por qué Orange en lugar de sklearn directo:**

Permite comparar visualmente varios modelos (Random Forest, Logistic Regression, SVM, kNN…) y sus métricas, matrices de confusión y curvas ROC sin escribir código. El modelo ganador se exporta como `.pkcls`; la **API** (FastAPI) lo carga vía la librería `Orange3` de Python (instalada en su contenedor). El frontend Streamlit solo consume el endpoint REST `/fase3/predecir` — no necesita `Orange3`.

---

## Diccionario clínico

Se aplica un **diccionario clínico** que normaliza las etiquetas crudas del LLM a **10 entidades estándar**, organizadas por prioridad Manchester:

```
Prioridad 1 — alarma vital (sospecha C1/C2)
  Disnea · Dolor_Torácico

Prioridad 2 — urgente (C2/C3)
  Fiebre · Dolor_Abdominal · Palpitaciones

Prioridad 3 — común (C3/C4)
  Cefalea · Náuseas_Vómitos

Prioridad 4 — leve (C4)
  Tos

Prioridad 5 — no urgente (C5)
  Fatiga · Dolor_Musculoesquelético
```

Cada caso se queda con **hasta 5 entidades**, ordenadas por gravedad (la principal es la de mayor prioridad). En el dataset se almacenan como texto separado por espacios y Orange genera el multi-hot binario con el widget *Bag of Words Binary*. Ver `dags/pipeline/diccionario_clinico.py`.

**Estadísticas del run actual** (270 casos · detalle completo en `data/processed/analisis_entidades.txt`):

```
Casos analizados:              270
Entidades activas:              10
Score de ansiedad medio:      0.34
Caso sin síntomas (Sin_Sintomas): 1

── Distribución Manchester ──────────────────────
  C1    0 casos  (  0.0%)  — ausente en el dataset (ver nota)
  C2   67 casos  ( 24.8%)  ██████
  C3  121 casos  ( 44.8%)  █████████████████████████████
  C4   55 casos  ( 20.4%)  █████████████
  C5   27 casos  ( 10.0%)  █████

── Entidades más frecuentes ─────────────────────
  Tos                  165 casos  (61.1%)
  Fatiga               140 casos  (51.9%)
  Fiebre               102 casos  (37.8%)
  Disnea                94 casos  (34.8%)
  Dolor_Musculoesquelético  84 casos  (31.1%)
  Dolor_Torácico        53 casos  (19.6%)
  Cefalea               43 casos  (15.9%)
  Náuseas_Vómitos       26 casos  ( 9.6%)
  Palpitaciones         20 casos  ( 7.4%)
  Dolor_Abdominal       19 casos  ( 7.0%)
```

> **Limitación conocida — C1 no es predecible con el modelo actual:**
> El dataset Fareez OSCE no incluye ningún caso de parada cardiorrespiratoria
> ni emergencia vital inmediata, por lo que los modelos entrenados sobre él
> **nunca predicen C1**. El rango real de predicción es **C2–C5**.
> Ante un audio con síntomas de máxima gravedad (Disnea + Dolor_Torácico
> intensos, pérdida de consciencia, etc.), el modelo devolverá **C2** como
> máximo. Para un uso clínico real sería necesario ampliar el dataset con
> casos C1 etiquetados.

---

## Esquema de la base de datos

La tabla **`entrevista`** (PostgreSQL) solo registra **predicciones en
tiempo real** de Fase 3 (audios subidos desde el frontend o llamadas REST a
la API). Los DAGs de Fase 1 NO escriben aquí.

```sql
guid_entrevista              VARCHAR(255) PRIMARY KEY
url_texto_original           VARCHAR(255)
motor_workflow               VARCHAR(50)   -- siempre 'API' (predicciones de Fase 3)
estado                       VARCHAR(50)   -- 'PREDICIENDO' | 'PREDICCION_COMPLETADA' | 'ERROR'

-- Timestamps por fase del pipeline:
inicio_solicitud             TIMESTAMP
fin_solicitud                TIMESTAMP
inicio_preprocesamiento      TIMESTAMP     -- Whisper inicio
fin_preprocesamiento         TIMESTAMP     -- Whisper fin
inicio_extraccion_entidades  TIMESTAMP     -- Mistral inicio
fin_extraccion_entidades     TIMESTAMP     -- Mistral fin
inicio_entrenamiento         TIMESTAMP     -- predict ML inicio
fin_entrenamiento            TIMESTAMP     -- predict ML fin
```

Cada predicción crea una fila y se va actualizando con los timestamps a lo largo del pipeline.

> **Nota:** el schema solo contiene las columnas realmente usadas. Se descartaron
> columnas heredadas del diseño inicial que nunca llegaron a tener valor:
> `url_dataset_generado`, `url_modelo_entrenado` (los modelos se gestionan en
> disco local, no en la BD), `inicio/fin_normalizacion`, `inicio/fin_etiquetado`
> (pasos que en la implementación final quedaron fusionados en la llamada a
> Mistral), `inicio/fin_score` (el score de ansiedad lo devuelve Mistral en la
> misma respuesta, no es un paso separado) y `workflow_id` (sin uso).

---

## Servicios y puertos

| Contenedor | Puerto host | Función |
|---|---|---|
| `triageia_postgres` | **5433** | PostgreSQL (db `triageia`) |
| `triageia_minio` | 9000 / 9001 | S3 storage / Console UI |
| `triageia_airflow_webserver` | 8080 | Airflow UI (admin/admin) |
| `triageia_airflow_scheduler` | — | Ejecuta DAGs |
| `triageia_api` | 8000 | FastAPI (Swagger en `/docs`) |
| `triageia_frontend` | 8501 | Streamlit |
| `triageia_grafana` | 3000 | Dashboard de tiempos por fase |

---

## Variables de entorno

Fichero `.env` (copia `.env.example` y rellena `MISTRAL_API_KEY`):

```env
MISTRAL_API_KEY=tu_clave_de_mistral_aqui

POSTGRES_DB=triageia
POSTGRES_USER=triageia
POSTGRES_PASSWORD=triageia

MINIO_ROOT_USER=minioadmin
MINIO_ROOT_PASSWORD=minioadmin
```

> Las credenciales de Airflow UI (`admin / admin`) están hardcoded en
> `docker-compose.yml` y no se configuran por env var.

---

## Notas técnicas

- **Whisper** descarga el modelo `base` (~140 MB) la primera vez que se usa
- **Orange3** se instala SOLO en el contenedor de la API (~500 MB de dependencias) para poder cargar el `.pkcls`. El frontend NO necesita Orange3.
- El modelo `.pkcls` puede dar **warnings de versión de sklearn** al cargarse (Orange Desktop usa una versión ligeramente distinta), pero funciona correctamente
- Los buckets de MinIO (`audios`, `textos`, `enriquecidos`, `datasets`) se crean automáticamente al arrancar
- La API monta `./models:/app/models` para detectar automáticamente cualquier `.pkcls` que guardes desde Orange en esa carpeta (hot-reload por `mtime`)

---

## Créditos

- Dataset: **Fareez et al. — OSCE clinical interviews** (270 casos)
- Protocolo: **Manchester Triage System (MTS)**
- Proyecto académico: TriageIA — CES IA-BD 25/26
