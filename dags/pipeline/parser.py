import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

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
    id: str
    categoria: str
    origen: str = "dataset"          # "train" | "test" | "Simulación"
    turnos: list[Turno] = field(default_factory=list)

    @property
    def transcripcion(self) -> str:
        return " ".join(t.text for t in self.turnos)

    @property
    def num_turnos(self) -> int:
        return len(self.turnos)


def parsear_archivo(ruta: Path, origen: str = "dataset") -> dict[str, Caso]:
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
                casos[caso_id] = Caso(id=caso_id, categoria=categoria, origen=origen)
            casos[caso_id].turnos.append(
                Turno(start=float(start), end=float(end), text=texto.strip())
            )
    for caso in casos.values():
        caso.turnos.sort(key=lambda t: t.start)
    return casos


def cargar_dataset(data_dir: Path) -> dict[str, Caso]:
    """
    Carga ambos .info. Si un caso aparece en los dos ficheros se queda con la
    versión 'train' (que es la más completa) pero marca origen='train+test'.
    """
    train = parsear_archivo(data_dir / "medical_train.info", origen="train")
    test  = parsear_archivo(data_dir / "medical_test.info",  origen="test")

    casos: dict[str, Caso] = {}
    # 1) Empezamos con los de train
    casos.update(train)
    # 2) Añadimos los de test que NO estén ya
    for guid, c in test.items():
        if guid in casos:
            casos[guid].origen = "train+test"
        else:
            casos[guid] = c
    return casos
