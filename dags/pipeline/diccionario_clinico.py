"""
Diccionario clínico estándar con 10 entidades agrupadas por prioridad
Manchester. Cada caso se mapea a la entidad más grave que aparezca.
"""

DICCIONARIO_CLINICO = {
    # ── Prioridad 1: alarma vital (C1/C2) ─────────────────────────────────
    "Disnea":                   1,
    "Dolor_Torácico":           1,
    # ── Prioridad 2: urgente (C2/C3) ─────────────────────────────────────
    "Fiebre":                   2,
    "Dolor_Abdominal":          2,
    "Palpitaciones":            2,
    # ── Prioridad 3: común (C3/C4) ───────────────────────────────────────
    "Cefalea":                  3,
    "Náuseas_Vómitos":          3,
    # ── Prioridad 4: leve (C4) ───────────────────────────────────────────
    "Tos":                      4,
    # ── Prioridad 5: no urgente (C5) ─────────────────────────────────────
    "Fatiga":                   5,
    "Dolor_Musculoesquelético": 5,
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

    # ── Fiebre (2) ────────────────────────────────────────────────────────
    "fiebre": "Fiebre", "fiebre/hipertermia": "Fiebre", "hipertermia": "Fiebre",
    "fiebre alta": "Fiebre", "febrícula": "Fiebre",
    "escalofríos": "Fiebre", "tiritona": "Fiebre",
    "sudoración nocturna": "Fiebre", "diaforesis": "Fiebre",
    "pérdida de peso": "Fiebre", "amigdalitis": "Fiebre",

    # ── Dolor abdominal (2) ───────────────────────────────────────────────
    "dolor abdominal": "Dolor_Abdominal", "dolor en el abdomen": "Dolor_Abdominal",
    "epigastralgia": "Dolor_Abdominal", "cólico abdominal": "Dolor_Abdominal",
    "dolor pélvico": "Dolor_Abdominal",

    # ── Palpitaciones (2) ─────────────────────────────────────────────────
    "palpitaciones": "Palpitaciones", "taquicardia": "Palpitaciones",
    "arritmia": "Palpitaciones",

    # ── Cefalea (3) ───────────────────────────────────────────────────────
    "cefalea": "Cefalea", "dolor de cabeza": "Cefalea", "migraña": "Cefalea",

    # ── Náuseas/Vómitos (3) ───────────────────────────────────────────────
    "náuseas": "Náuseas_Vómitos", "vómitos": "Náuseas_Vómitos",
    "náuseas/vómitos": "Náuseas_Vómitos", "arcadas": "Náuseas_Vómitos",
    "regurgitación": "Náuseas_Vómitos",

    # ── Tos (4) ───────────────────────────────────────────────────────────
    "tos": "Tos", "tos seca": "Tos", "tos crónica": "Tos",
    "tos persistente": "Tos", "tos productiva": "Tos",

    # ── Fatiga (5) ────────────────────────────────────────────────────────
    "fatiga": "Fatiga", "cansancio": "Fatiga", "astenia": "Fatiga",
    "debilidad": "Fatiga", "letargo": "Fatiga",

    # ── Dolor musculoesquelético (5) ──────────────────────────────────────
    "dolor musculoesquelético": "Dolor_Musculoesquelético",
    "dolor muscular": "Dolor_Musculoesquelético",
    "dolor articular": "Dolor_Musculoesquelético",
    "dolor cervical": "Dolor_Musculoesquelético",
    "dolor lumbar": "Dolor_Musculoesquelético",
    "lumbago": "Dolor_Musculoesquelético",
    "dolor de espalda": "Dolor_Musculoesquelético",
    "rigidez articular": "Dolor_Musculoesquelético",
}


def normalizar_entidades(ents_raw: list, max_n: int = 5) -> list:
    """
    Mapea entidades crudas al diccionario y devuelve hasta `max_n` entidades
    estándar, ordenadas por prioridad clínica (1 = más grave primero).

    - Mínimo: 0 si no hay match (se asume el caso es leve / asintomático)
    - Máximo: 5 (por defecto). Las más graves prevalecen.
    """
    estandar = set()
    for e in ents_raw:
        original = (e or "").strip()
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
