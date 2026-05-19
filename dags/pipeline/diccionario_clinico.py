"""
Diccionario clínico estándar con 20 entidades agrupadas por prioridad
Manchester. Cada caso se mapea a la entidad más grave que aparezca.
"""

DICCIONARIO_CLINICO = {
    # ── Prioridad 1: alarma vital (C1/C2) ─────────────────────────────────
    "Disnea":                  1,
    "Dolor_Torácico":          1,
    "Síncope":                 1,
    "Hemoptisis":              1,
    # ── Prioridad 2: urgente (C2/C3) ─────────────────────────────────────
    "Sibilancias":             2,
    "Palpitaciones":           2,
    "Dolor_Abdominal":         2,
    "Fiebre":                  2,
    "Mareo":                   2,
    # ── Prioridad 3: común (C3/C4) ───────────────────────────────────────
    "Tos":                     3,
    "Náuseas_Vómitos":         3,
    "Cefalea":                 3,
    "Diarrea":                 3,
    "Edema":                   3,
    "Odinofagia":              3,
    "Congestión_Respiratoria": 3,
    # ── Prioridad 4: leve (C4/C5) ────────────────────────────────────────
    "Fatiga":                  4,
    "Dolor_Musculoesquelético":4,
    "Anosmia":                 4,
    "Traumatismo":             4,
}


MAPEO_ENTIDADES = {
    # ── Disnea (1) ─────────────────────────────────────────────────────────
    "disnea": "Disnea", "disnea de esfuerzo": "Disnea", "disnea de reposo": "Disnea",
    "dificultad respiratoria": "Disnea", "dificultad para respirar": "Disnea",
    "ortopnea": "Disnea", "broncoespasmo": "Disnea", "estridor": "Disnea",
    "cianosis": "Disnea",

    # ── Dolor torácico (1) ────────────────────────────────────────────────
    "dolor torácico": "Dolor_Torácico", "dolor torácico agudo": "Dolor_Torácico",
    "dolor torácico opresivo": "Dolor_Torácico", "dolor en el pecho": "Dolor_Torácico",
    "presión torácica": "Dolor_Torácico", "opresión torácica": "Dolor_Torácico",
    "dolor irradiado": "Dolor_Torácico", "dolor precordial": "Dolor_Torácico",

    # ── Síncope (1) ───────────────────────────────────────────────────────
    "síncope": "Síncope", "pérdida de conciencia": "Síncope", "desmayo": "Síncope",
    "lipotimia": "Síncope", "alteración conciencia": "Síncope",
    "confusión": "Síncope", "desorientación": "Síncope",

    # ── Hemoptisis (1) ────────────────────────────────────────────────────
    "hemoptisis": "Hemoptisis", "expectoración hemoptoica": "Hemoptisis",
    "esputo con sangre": "Hemoptisis", "expectoración con sangre": "Hemoptisis",

    # ── Sibilancias (2) ───────────────────────────────────────────────────
    "sibilancias": "Sibilancias", "pitos": "Sibilancias",
    "tos productiva": "Sibilancias",

    # ── Palpitaciones (2) ─────────────────────────────────────────────────
    "palpitaciones": "Palpitaciones", "taquicardia": "Palpitaciones",
    "arritmia": "Palpitaciones",

    # ── Dolor abdominal (2) ───────────────────────────────────────────────
    "dolor abdominal": "Dolor_Abdominal", "dolor en el abdomen": "Dolor_Abdominal",
    "epigastralgia": "Dolor_Abdominal", "cólico abdominal": "Dolor_Abdominal",
    "dolor pélvico": "Dolor_Abdominal",

    # ── Fiebre (2) ────────────────────────────────────────────────────────
    "fiebre": "Fiebre", "fiebre/hipertermia": "Fiebre", "hipertermia": "Fiebre",
    "fiebre alta": "Fiebre", "febrícula": "Fiebre",
    "escalofríos": "Fiebre", "tiritona": "Fiebre",
    "sudoración nocturna": "Fiebre", "diaforesis": "Fiebre",
    "pérdida de peso": "Fiebre", "amigdalitis": "Fiebre",

    # ── Mareo (2) ─────────────────────────────────────────────────────────
    "mareo": "Mareo", "mareo/vértigo": "Mareo", "vértigo": "Mareo",
    "inestabilidad": "Mareo",

    # ── Tos (3) ───────────────────────────────────────────────────────────
    "tos": "Tos", "tos seca": "Tos", "tos crónica": "Tos",
    "tos persistente": "Tos",

    # ── Náuseas/Vómitos (3) ───────────────────────────────────────────────
    "náuseas": "Náuseas_Vómitos", "vómitos": "Náuseas_Vómitos",
    "náuseas/vómitos": "Náuseas_Vómitos", "arcadas": "Náuseas_Vómitos",
    "regurgitación": "Náuseas_Vómitos",

    # ── Cefalea (3) ───────────────────────────────────────────────────────
    "cefalea": "Cefalea", "dolor de cabeza": "Cefalea", "migraña": "Cefalea",

    # ── Diarrea (3) ───────────────────────────────────────────────────────
    "diarrea": "Diarrea", "deposiciones líquidas": "Diarrea",
    "heces blandas": "Diarrea",

    # ── Edema (3) ─────────────────────────────────────────────────────────
    "edema": "Edema", "edema/inflamación": "Edema",
    "inflamación": "Edema", "hinchazón": "Edema",

    # ── Odinofagia (3) ────────────────────────────────────────────────────
    "odinofagia": "Odinofagia", "dolor de garganta": "Odinofagia",
    "disfagia": "Odinofagia", "dificultad para tragar": "Odinofagia",

    # ── Congestión respiratoria (3) ───────────────────────────────────────
    "rinorrea": "Congestión_Respiratoria", "congestión nasal": "Congestión_Respiratoria",
    "congestión respiratoria": "Congestión_Respiratoria",
    "secreción nasal": "Congestión_Respiratoria", "expectoración": "Congestión_Respiratoria",
    "moqueo": "Congestión_Respiratoria",

    # ── Fatiga (4) ────────────────────────────────────────────────────────
    "fatiga": "Fatiga", "cansancio": "Fatiga", "astenia": "Fatiga",
    "debilidad": "Fatiga", "letargo": "Fatiga",

    # ── Dolor musculoesquelético (4) ──────────────────────────────────────
    "dolor musculoesquelético": "Dolor_Musculoesquelético",
    "dolor muscular": "Dolor_Musculoesquelético",
    "dolor articular": "Dolor_Musculoesquelético",
    "dolor cervical": "Dolor_Musculoesquelético",
    "dolor lumbar": "Dolor_Musculoesquelético",
    "lumbago": "Dolor_Musculoesquelético",
    "dolor de espalda": "Dolor_Musculoesquelético",
    "rigidez articular": "Dolor_Musculoesquelético",

    # ── Anosmia (4) ───────────────────────────────────────────────────────
    "anosmia": "Anosmia", "hiposmia": "Anosmia",
    "pérdida de olfato": "Anosmia", "ageusia": "Anosmia",

    # ── Traumatismo (4) ───────────────────────────────────────────────────
    "traumatismo": "Traumatismo", "esguince": "Traumatismo",
    "fractura": "Traumatismo", "contusión": "Traumatismo",
    "luxación": "Traumatismo", "herida": "Traumatismo",
    "erupción cutánea": "Traumatismo", "rash": "Traumatismo",
    "exantema": "Traumatismo", "lesión cutánea": "Traumatismo",
}


def normalizar_entidades(ents_raw: list, max_n: int = 1) -> list:
    """
    Mapea entidades crudas al diccionario y devuelve hasta `max_n` entidades
    ordenadas por prioridad clínica (1 = más grave primero).
    """
    estandar = set()
    for e in ents_raw:
        original = (e or "").strip()
        # Si ya viene en el vocabulario cerrado (p.ej. "Dolor_Torácico"),
        # se acepta tal cual sin recurrir a MAPEO_ENTIDADES.
        if original in DICCIONARIO_CLINICO:
            estandar.add(original)
            continue
        k = original.lower()
        if k in MAPEO_ENTIDADES:
            estandar.add(MAPEO_ENTIDADES[k])
        else:
            for key, val in MAPEO_ENTIDADES.items():
                if key in k and len(key) >= 5:
                    estandar.add(val)
                    break
    if not estandar:
        return []
    return sorted(estandar, key=lambda x: (DICCIONARIO_CLINICO.get(x, 99), x))[:max_n]
