"""Registro de directos ya procesados, para no repetir trabajo."""
import json
import time

from .util import RAIZ

ARCHIVO = RAIZ / "estado" / "procesados.json"


class Estado:
    def __init__(self):
        if ARCHIVO.exists():
            self.datos = json.loads(ARCHIVO.read_text(encoding="utf-8"))
        else:
            self.datos = {"procesados": {}}
        self.datos.setdefault("procesados", {})

    def ya_procesado(self, vod_id: str) -> bool:
        return vod_id in self.datos["procesados"]

    def marcar(self, vod_id: str, streamer: str, titulo: str, clips: int) -> None:
        self.datos["procesados"][vod_id] = {
            "streamer": streamer,
            "titulo": titulo,
            "clips": clips,
            "fecha": time.strftime("%Y-%m-%d %H:%M"),
        }
        self.guardar()

    def guardar(self) -> None:
        ARCHIVO.parent.mkdir(exist_ok=True)
        ARCHIVO.write_text(
            json.dumps(self.datos, ensure_ascii=False, indent=2), encoding="utf-8"
        )
