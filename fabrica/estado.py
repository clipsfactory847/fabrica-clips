"""Registro de lo ya procesado, para no repetir trabajo ni mandarte el mismo clip dos veces."""
import json
import time

from .util import RAIZ

ARCHIVO = RAIZ / "estado" / "procesados.json"


class Estado:
    archivo = ARCHIVO

    def __init__(self):
        if self.archivo.exists():
            self.datos = json.loads(self.archivo.read_text(encoding="utf-8"))
        else:
            self.datos = {}
        self.datos.setdefault("procesados", {})   # directos completos
        self.datos.setdefault("clips", {})        # clips de Twitch ya revisados
        self.datos.setdefault("telegram_offset", 0)

    # --- directos ---
    def ya_procesado(self, vod_id: str) -> bool:
        return vod_id in self.datos["procesados"]

    def marcar(self, vod_id: str, streamer: str, titulo: str, clips: int) -> None:
        self.datos["procesados"][vod_id] = {
            "streamer": streamer, "titulo": titulo, "clips": clips,
            "fecha": time.strftime("%Y-%m-%d %H:%M"),
        }
        self.guardar()

    # --- clips de Twitch ---
    def clip_visto(self, clip_id: str) -> bool:
        return clip_id in self.datos["clips"]

    def marcar_clip(self, clip_id: str, **info) -> None:
        actual = self.datos["clips"].get(clip_id, {})
        actual.update(info)
        actual.setdefault("fecha", time.strftime("%Y-%m-%d %H:%M"))
        self.datos["clips"][clip_id] = actual
        self.guardar()

    def limpiar_antiguos(self, maximo: int = 3000) -> None:
        clips = self.datos["clips"]
        if len(clips) > maximo:
            for k in sorted(clips, key=lambda k: clips[k].get("fecha", ""))[: len(clips) - maximo]:
                del clips[k]

    def guardar(self) -> None:
        self.archivo.parent.mkdir(exist_ok=True)
        self.archivo.write_text(json.dumps(self.datos, ensure_ascii=False, indent=2), encoding="utf-8")


class Decisiones(Estado):
    """Lo que decides en Telegram (aprobar/descartar) y lo ya publicado.
    Va en un archivo aparte para no chocar con el proceso que crea los clips."""
    archivo = RAIZ / "estado" / "decisiones.json"
