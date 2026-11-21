"""Convierte la voz en texto (con el tiempo exacto de cada palabra) usando Groq."""
import time
from pathlib import Path

import requests

from .util import ejecutar, log, secreto

URL = "https://api.groq.com/openai/v1/audio/transcriptions"
MODELO = "whisper-large-v3"


def _recortar_audio(audio: Path, inicio: float, duracion: float, destino: Path) -> Path:
    ejecutar(["ffmpeg", "-y", "-v", "error", "-ss", f"{inicio:.2f}", "-t", f"{duracion:.2f}",
              "-i", str(audio), "-ac", "1", "-ar", "16000", "-b:a", "48k", str(destino)])
    return destino


def transcribir_trozo(audio: Path, inicio: float, fin: float, idioma: str, carpeta: Path) -> dict:
    """Devuelve {'palabras': [...], 'frases': [...]} con tiempos absolutos del directo."""
    trozo = _recortar_audio(audio, inicio, fin - inicio, carpeta / f"trozo_{int(inicio)}.mp3")
    clave = secreto("GROQ_API_KEY")

    for intento in range(8):
        with open(trozo, "rb") as f:
            r = requests.post(
                URL,
                headers={"Authorization": f"Bearer {clave}"},
                files={"file": (trozo.name, f, "audio/mpeg")},
                data=[
                    ("model", MODELO),
                    ("language", idioma),
                    ("response_format", "verbose_json"),
                    ("timestamp_granularities[]", "word"),
                    ("timestamp_granularities[]", "segment"),
                    ("temperature", "0"),
                ],
                timeout=300,
            )
        if r.status_code == 429 or r.status_code >= 500:
            espera = min(float(r.headers.get("retry-after", 30)), 600)
            log(f"Groq pide esperar {espera:.0f} s (límite gratuito). Esperando...")
            time.sleep(espera + 1)
            continue
        if r.status_code != 200:
            raise RuntimeError(f"Error de Groq ({r.status_code}): {r.text[:500]}")
        datos = r.json()
        break
    else:
        raise RuntimeError("Groq no respondió tras varios intentos.")

    palabras = [
        {"t0": inicio + w["start"], "t1": inicio + w["end"], "w": w["word"].strip()}
        for w in datos.get("words") or [] if w.get("word", "").strip()
    ]
    frases = [
        {"t0": inicio + s["start"], "t1": inicio + s["end"], "texto": s["text"].strip()}
        for s in datos.get("segments") or [] if s.get("text", "").strip()
    ]
    # Si no vienen palabras sueltas, las repartimos dentro de cada frase.
    if not palabras:
        for s in frases:
            trozos = s["texto"].split()
            paso = (s["t1"] - s["t0"]) / max(1, len(trozos))
            for i, w in enumerate(trozos):
                palabras.append({"t0": s["t0"] + i * paso, "t1": s["t0"] + (i + 1) * paso, "w": w})
    time.sleep(3)  # respetar el límite de peticiones por minuto
    return {"palabras": palabras, "frases": frases}
