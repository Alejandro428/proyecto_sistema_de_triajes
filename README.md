# TriageIA — Sistema de Triaje Médico con IA

Proyecto del Curso de Especialización IA-BD 25/26.
Herramienta de soporte a la decisión clínica que transforma la voz del paciente en una prioridad médica estructurada siguiendo el **Protocolo Manchester (C1–C5)**.

---

## Descripción

TriageIA procesa conversaciones clínicas reales, extrae síntomas, los normaliza a terminología médica estándar y predice el nivel de urgencia del paciente. El sistema incorpora un score de ansiedad para auditar si el modelo prioriza la clínica sobre el estado emocional del paciente.

Basado en el corpus de **Fareez et al. (2022)** — 272 entrevistas clínicas simuladas (metodología OSCE), publicado en *Nature Scientific Data*.

---

## Arquitectura

El sistema está compuesto por 5 servicios Docker con responsabilidades separadas:

| Servicio | Puerto | Rol |
|---|---|---|
| `api` | 8000 | Backend central: generación del dataset (F1) y predicción Manchester (F3) |
| `whisper` | 8001 | Transcripción de audio a texto (modelo Whisper de OpenAI) |
| `streamlit` | 8501 | Interfaz del médico — sube audio y recibe el triaje en tiempo real |
| `postgres` | 5432 | Persistencia: tabla de casos (Ground Truth) y log de auditoría ética |
| `mlflow` | 5000 | Registro y comparación de experimentos de entrenamiento (Fase 2) |

```
streamlit ──▶ api ──▶ whisper
               │
            postgres
               │
            mlflow  (durante entrenamiento)
```

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

**Fase 1 — Ingeniería de datos**
Parseo de transcripciones, diseño del prompt LLM con few-shot, normalización semántica al diccionario clínico y generación del CSV Maestro (Ground Truth).

**Fase 2 — Modelado**
Entrenamiento de un clasificador multiclase Manchester. Gestión del desbalance de clases (SMOTE / class weights). Integración del score de ansiedad. Comparación de experimentos con MLflow.

**Fase 3 — MVP y auditoría**
Dashboard Streamlit para uso clínico en tiempo real. Auditoría ética de casos de under-triage y propuesta de acciones correctivas.

---

## Estructura del proyecto

```
proyecto_sistema_de_triajes/
├── data/
│   ├── raw/            # Transcripciones originales del dataset
│   ├── processed/      # CSV Maestro generado en Fase 1
│   └── audio/          # Archivos de audio (no versionados)
├── models/             # Modelos entrenados en Fase 2 (no versionados)
├── notebooks/          # Exploración y desarrollo
├── services/
│   ├── api/            # FastAPI — lógica de negocio principal
│   ├── whisper/        # Servicio de transcripción de audio
│   ├── streamlit/      # Dashboard MVP
│   └── mlflow/         # Tracking de experimentos
├── docs/               # Enunciados del proyecto
├── docker-compose.yml
└── .env.example
```

---

## Puesta en marcha

```bash
# 1. Copia el archivo de variables de entorno y añade tu API key
cp .env.example .env

# 2. Construye y arranca todos los servicios
docker-compose up --build

# 3. Accede a los servicios
#    Dashboard médico: http://localhost:8501
#    API docs:         http://localhost:8000/docs
#    MLflow:           http://localhost:5000
```

---

## Dataset

Fareez et al. (2022). *A dataset of simulated patient-physician medical interviews.*
Nature Scientific Data. [Paper](https://www.nature.com/articles/s41597-022-01423-1)
