"""Primer filtro: encontrar en el audio los momentos donde 'pasa algo'
(gritos, risas, subidas de volumen) para no tener que transcribir horas enteras."""
import subprocess

import numpy as np

from .util import log

FRECUENCIA = 8000


def volumen_por_segundo(audio) -> np.ndarray:
    """Devuelve el volumen (en dB) de cada segundo del directo."""
    cmd = ["ffmpeg", "-v", "error", "-i", str(audio),
           "-ac", "1", "-ar", str(FRECUENCIA), "-f", "s16le", "-"]
    proceso = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    valores = []
    bloque = FRECUENCIA * 2 * 60  # leemos de minuto en minuto
    while True:
        datos = proceso.stdout.read(bloque)
        if not datos:
            break
        a = np.frombuffer(datos, dtype=np.int16).astype(np.float32)
        n = len(a) // FRECUENCIA
        if n == 0:
            break
        a = a[: n * FRECUENCIA].reshape(n, FRECUENCIA)
        rms = np.sqrt((a ** 2).mean(axis=1)) + 1e-6
        valores.extend(20 * np.log10(rms / 32768))
    proceso.wait()
    return np.array(valores)


def _media_movil(x: np.ndarray, ventana: int) -> np.ndarray:
    ventana = max(1, min(ventana, len(x)))
    acumulado = np.cumsum(np.insert(x, 0, 0.0))
    medias = (acumulado[ventana:] - acumulado[:-ventana]) / ventana
    relleno_ini = ventana // 2
    relleno_fin = len(x) - len(medias) - relleno_ini
    return np.concatenate([np.full(relleno_ini, medias[0]), medias, np.full(relleno_fin, medias[-1])])


def momentos_candidatos(volumen: np.ndarray, cuantos: int = 18,
                        antes: int = 120, despues: int = 60, separacion: int = 240) -> list:
    """Elige los trozos del directo con más 'subidones' respecto a lo normal.
    Devuelve una lista de (inicio, fin) en segundos."""
    total = len(volumen)
    if total < 60:
        return [(0, total)] if total > 15 else []

    volumen = np.maximum(volumen, -60)
    corto = _media_movil(volumen, 8)       # lo que pasa ahora
    normal = _media_movil(volumen, 300)    # lo habitual en esos 5 minutos
    puntuacion = corto - normal
    puntuacion[corto < -50] = -99          # ignorar silencios

    elegidos = []
    for pico in np.argsort(puntuacion)[::-1]:
        if puntuacion[pico] <= 0 or len(elegidos) >= cuantos:
            break
        if all(abs(pico - p) >= separacion for p in elegidos):
            elegidos.append(int(pico))

    trozos = []
    for pico in sorted(elegidos):
        inicio = max(0, pico - antes)
        fin = min(total, pico + despues)
        trozos.append((inicio, fin))
    log(f"Encontrados {len(trozos)} momentos candidatos en {total // 60} minutos de directo.")
    return trozos
