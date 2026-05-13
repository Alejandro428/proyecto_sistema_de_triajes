-- Tabla de seguimiento del workflow (una fila por entrevista, rastrea cada etapa)
CREATE TABLE IF NOT EXISTS entrevista (
    guid_entrevista          VARCHAR(50)  PRIMARY KEY,
    origen                   VARCHAR(20)  NOT NULL DEFAULT 'Dataset',
    url_texto_original       VARCHAR(500),
    url_dataset_generado     VARCHAR(500),
    url_modelo_entrenado     VARCHAR(500),
    estado                   VARCHAR(50)  DEFAULT 'CREADA',
    motor_workflow           VARCHAR(50)  DEFAULT 'Airflow',
    workflow_id              VARCHAR(255),
    inicio_solicitud         TIMESTAMP,
    fin_solicitud            TIMESTAMP,
    inicio_preprocesamiento  TIMESTAMP,
    fin_preprocesamiento     TIMESTAMP,
    inicio_extraccion        TIMESTAMP,
    fin_extraccion           TIMESTAMP,
    inicio_normalizacion     TIMESTAMP,
    fin_normalizacion        TIMESTAMP,
    inicio_etiquetado        TIMESTAMP,
    fin_etiquetado           TIMESTAMP,
    inicio_score             TIMESTAMP,
    fin_score                TIMESTAMP,
    inicio_entrenamiento     TIMESTAMP,
    fin_entrenamiento        TIMESTAMP,
    creado_en                TIMESTAMP    DEFAULT NOW()
);

-- Tabla con los datos enriquecidos de cada entrevista
CREATE TABLE IF NOT EXISTS casos (
    guid_entrevista      VARCHAR(50)  PRIMARY KEY REFERENCES entrevista(guid_entrevista),
    transcripcion        TEXT         NOT NULL,
    resumen              TEXT,
    sintomas_detectados  JSONB,
    terminos_clinicos    JSONB,
    nivel_urgencia       VARCHAR(2),
    razonamiento         TEXT,
    nivel_ansiedad       NUMERIC(4,2),
    prediccion_entrenada VARCHAR(2)
);

-- Tabla de predicciones Fase 2
CREATE TABLE IF NOT EXISTS predicciones (
    id                  SERIAL       PRIMARY KEY,
    guid_entrevista     VARCHAR(50)  REFERENCES entrevista(guid_entrevista),
    nivel_predicho      VARCHAR(2)   NOT NULL,
    nivel_urgencia_real VARCHAR(2),
    resultado           VARCHAR(20),
    creado_en           TIMESTAMP    DEFAULT NOW()
);
