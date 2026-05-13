-- Tabla de casos clínicos procesados en Fase 1 (Ground Truth)
CREATE TABLE IF NOT EXISTS casos (
    id                  SERIAL PRIMARY KEY,
    id_caso             VARCHAR(20)  NOT NULL UNIQUE,
    categoria           VARCHAR(5)   NOT NULL,
    transcripcion       TEXT         NOT NULL,
    resumen             TEXT,
    sintomas_detectados JSONB,
    terminos_clinicos   JSONB,
    nivel_urgencia      VARCHAR(2),
    razonamiento        TEXT,
    nivel_ansiedad      NUMERIC(4,2),
    creado_en           TIMESTAMP    DEFAULT NOW()
);

-- Tabla de predicciones del MVP en Fase 3 (log de auditoría)
CREATE TABLE IF NOT EXISTS predicciones (
    id                  SERIAL PRIMARY KEY,
    timestamp           TIMESTAMP    DEFAULT NOW(),
    transcripcion       TEXT         NOT NULL,
    terminos_clinicos   JSONB,
    nivel_ansiedad      NUMERIC(4,2),
    nivel_predicho      VARCHAR(2)   NOT NULL,
    nivel_urgencia_real VARCHAR(2),
    resultado           VARCHAR(20),
    creado_en           TIMESTAMP    DEFAULT NOW()
);
