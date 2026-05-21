CREATE TABLE IF NOT EXISTS Entrevista (
    GUID_Entrevista             VARCHAR(255) PRIMARY KEY,
    URL_Texto_Original          VARCHAR(255),
    Inicio_Solicitud            TIMESTAMP,
    Fin_Solicitud               TIMESTAMP,
    Inicio_Preprocesamiento     TIMESTAMP,
    Fin_Preprocesamiento        TIMESTAMP,
    Inicio_Extraccion_Entidades TIMESTAMP,
    Fin_Extraccion_Entidades    TIMESTAMP,
    Inicio_Entrenamiento        TIMESTAMP,
    Fin_Entrenamiento           TIMESTAMP,
    Motor_Workflow              VARCHAR(50),
    Estado                      VARCHAR(50)
);
