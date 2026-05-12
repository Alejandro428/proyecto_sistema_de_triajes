-- Tabla de casos clínicos procesados en Fase 1 (Ground Truth)
CREATE TABLE IF NOT EXISTS casos (
    id              SERIAL PRIMARY KEY,
    id_caso         VARCHAR(20)  NOT NULL UNIQUE,
    categoria       VARCHAR(5)   NOT NULL,
    origen          VARCHAR(10)  NOT NULL,
    texto_original  TEXT         NOT NULL,
    resumen_es      TEXT,
    entidades       JSONB,
    entidades_norm  JSONB,
    triage_real     VARCHAR(2),
    justificacion   TEXT,
    score_ansiedad  NUMERIC(4,2),
    creado_en       TIMESTAMP    DEFAULT NOW()
);

-- Tabla de predicciones del MVP en Fase 3 (log de auditoría)
CREATE TABLE IF NOT EXISTS predicciones (
    id              SERIAL PRIMARY KEY,
    timestamp       TIMESTAMP    DEFAULT NOW(),
    texto_audio     TEXT         NOT NULL,
    entidades       JSONB,
    score_ansiedad  NUMERIC(4,2),
    prediccion      VARCHAR(2)   NOT NULL,
    ground_truth    VARCHAR(2),
    validacion      VARCHAR(20),
    creado_en       TIMESTAMP    DEFAULT NOW()
);
