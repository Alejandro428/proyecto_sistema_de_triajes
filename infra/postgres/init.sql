-- Tabla de casos clínicos procesados en Fase 1 (Ground Truth)
CREATE TABLE IF NOT EXISTS casos (
    id              SERIAL PRIMARY KEY,
    id_caso         VARCHAR(20)  NOT NULL UNIQUE,   -- ej: RES0134
    categoria       VARCHAR(5)   NOT NULL,           -- RES, MSK, CAR, GAS
    origen          VARCHAR(10)  NOT NULL,           -- train / test
    texto_original  TEXT         NOT NULL,
    resumen_es      TEXT,
    entidades       JSONB,
    entidades_norm  JSONB,
    triage_real     VARCHAR(2),                      -- C1 … C5
    justificacion   TEXT,
    creado_en       TIMESTAMP    DEFAULT NOW()
);

-- Tabla de predicciones del MVP en Fase 3 (log de auditoría)
CREATE TABLE IF NOT EXISTS predicciones (
    id              SERIAL PRIMARY KEY,
    timestamp       TIMESTAMP    DEFAULT NOW(),
    texto_audio     TEXT         NOT NULL,
    entidades       JSONB,
    score_ansiedad  NUMERIC(4,2),
    prediccion      VARCHAR(2)   NOT NULL,           -- C1 … C5
    ground_truth    VARCHAR(2),                      -- se rellena si se valida después
    validacion      VARCHAR(20),                     -- Acierto / Under-triage / Over-triage
    creado_en       TIMESTAMP    DEFAULT NOW()
);
