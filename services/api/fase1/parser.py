"""
Fase 1 - Paso 1: Parser de archivos .info
Reconstruye las conversaciones clínicas completas agrupando
y ordenando los fragmentos de cada caso por timestamp.
"""

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path


LINE_PATTERN = re.compile(
    r"audio/([A-Z]+)(\d+)\.mp3\[(\d+\.\d+),(\d+\.\d+)\]\t(.+)"
)


@dataclass
class Turno:
    start: float
    end: float
    text: str


@dataclass
class Caso:
    id: str                      # ej: RES0134
    categoria: str               # RES, MSK, CAR, GAS
    origen: str                  # train / test
    turnos: list[Turno] = field(default_factory=list)

    @property
    def texto_completo(self) -> str:
        return " ".join(t.text for t in self.turnos)

    @property
    def num_turnos(self) -> int:
        return len(self.turnos)


def parsear_archivo(ruta: Path, origen: str) -> dict[str, Caso]:
    casos: dict[str, Caso] = {}

    with open(ruta, encoding="utf-8") as f:
        for linea in f:
            linea = linea.strip()
            if not linea:
                continue

            match = LINE_PATTERN.match(linea)
            if not match:
                continue

            categoria, numero, start, end, texto = match.groups()
            caso_id = f"{categoria}{numero}"

            if caso_id not in casos:
                casos[caso_id] = Caso(
                    id=caso_id,
                    categoria=categoria,
                    origen=origen,
                )

            casos[caso_id].turnos.append(
                Turno(start=float(start), end=float(end), text=texto.strip())
            )

    # Ordenar cada caso por timestamp de inicio
    for caso in casos.values():
        caso.turnos.sort(key=lambda t: t.start)

    return casos


def cargar_dataset(data_dir: Path) -> dict[str, Caso]:
    """Carga y combina train y test en un único diccionario."""
    casos = {}
    casos.update(parsear_archivo(data_dir / "medical_train.info", origen="train"))
    casos.update(parsear_archivo(data_dir / "medical_test.info", origen="test"))
    return casos


if __name__ == "__main__":
    data_dir = Path(__file__).parents[3] / "data" / "raw"
    casos = cargar_dataset(data_dir)

    # Resumen general
    categorias = defaultdict(int)
    for caso in casos.values():
        categorias[caso.categoria] += 1

    print(f"\nTotal de casos cargados: {len(casos)}")
    print(f"Distribucion por categoria:")
    for cat, n in sorted(categorias.items()):
        print(f"  {cat}: {n} casos")

    # Ejemplo: mostrar la conversación completa de un caso
    ejemplo_id = "RES0001"
    if ejemplo_id in casos:
        caso = casos[ejemplo_id]
        print(f"\nEjemplo — {caso.id} ({caso.categoria}, {caso.origen})")
        print(f"Turnos: {caso.num_turnos}")
        print(f"Texto completo:\n{caso.texto_completo[:600]}...")
