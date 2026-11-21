"""Todo lo relacionado con Twitch: buscar directos y descargar trozos."""
import json
import subprocess
from pathlib import Path

from .util import ejecutar, log

YTDLP = ["yt-dlp", "--no-warnings", "--no-progress"]


def esta_en_directo(usuario: str) -> bool:
    r = subprocess.run(
        YTDLP + ["-J", "--skip-download", f"https://www.twitch.tv/{usuario}"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return False  # si no está en directo, yt-dlp da error
    try:
        return bool(json.loads(r.stdout).get("is_live"))
    except ValueError:
        return False


def listar_directos(usuario: str, cuantos: int = 5) -> list:
    """Devuelve los últimos directos guardados (los más nuevos primero)."""
    url = f"https://www.twitch.tv/{usuario}/videos?filter=archives&sort=time"
    salida = ejecutar(YTDLP + ["-J", "--flat-playlist", "--playlist-end", str(cuantos), url])
    datos = json.loads(salida)
    directos = []
    for e in datos.get("entries") or []:
        enlace = e.get("url") or f"https://www.twitch.tv/videos/{str(e['id']).lstrip('v')}"
        directos.append({
            "id": str(e.get("id")),
            "titulo": e.get("title") or "Directo",
            "enlace": enlace,
            "duracion": e.get("duration"),
        })
    return directos


def info_directo(enlace: str) -> dict:
    datos = json.loads(ejecutar(YTDLP + ["-J", "--skip-download", enlace]))
    return {
        "id": str(datos.get("id")),
        "titulo": datos.get("title") or "Directo",
        "enlace": enlace,
        "duracion": datos.get("duration"),
        "streamer": datos.get("uploader_id") or datos.get("uploader") or "",
    }


def descargar_audio(enlace: str, carpeta: Path) -> Path:
    """Descarga solo el sonido del directo (mucho más ligero que el vídeo)."""
    log("Descargando el audio del directo...")
    plantilla = str(carpeta / "audio.%(ext)s")
    ejecutar(YTDLP + [
        "-f", "Audio_Only/audio_only/bestaudio/worst",
        "--concurrent-fragments", "8",
        "-o", plantilla, enlace,
    ])
    archivos = [p for p in carpeta.glob("audio.*") if p.suffix != ".part"]
    if not archivos:
        raise RuntimeError("No se pudo descargar el audio del directo.")
    log(f"Audio descargado: {archivos[0].name} ({archivos[0].stat().st_size // 1_000_000} MB)")
    return archivos[0]


def descargar_clip(clip: dict, destino: Path) -> Path:
    """Descarga un clip de Twitch. Si falla, lo saca del directo original (si existe)."""
    try:
        ejecutar(YTDLP + ["-f", "best[height<=1080]/best", "-o", str(destino), clip["url"]])
        if destino.exists():
            return destino
    except RuntimeError as e:
        log(f"No se pudo descargar el clip directamente ({str(e)[:120]}). Pruebo desde el directo...")
    if clip.get("video_id") and clip.get("vod_offset") is not None:
        ini = max(0, float(clip["vod_offset"]))
        return descargar_trozo(f"https://www.twitch.tv/videos/{clip['video_id']}",
                               ini, ini + float(clip.get("duration", 30)), destino)
    raise RuntimeError("No se pudo descargar el clip.")


def descargar_trozo(enlace: str, inicio: float, fin: float, destino: Path) -> Path:
    """Descarga solo el trozo de vídeo que necesitamos, en la mejor calidad hasta 1080p."""
    ejecutar(YTDLP + [
        "-f", "best[height<=1080]/best",
        "--download-sections", f"*{inicio:.2f}-{fin:.2f}",
        "--concurrent-fragments", "8",
        "-o", str(destino), enlace,
    ])
    if not destino.exists():
        candidatos = list(destino.parent.glob(destino.stem + "*"))
        candidatos = [c for c in candidatos if c.suffix != ".part"]
        if not candidatos:
            raise RuntimeError("No se pudo descargar el trozo de vídeo.")
        return candidatos[0]
    return destino
