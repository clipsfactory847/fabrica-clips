"""Pequeñas utilidades comunes."""
import json
import os
import subprocess
import time
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parent.parent


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def cargar_config() -> dict:
    with open(RAIZ / "config" / "cuentas.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def secreto(nombre: str) -> str:
    valor = os.environ.get(nombre, "").strip()
    if not valor:
        raise RuntimeError(
            f"Falta el secreto {nombre}. Revisa que esté guardado en GitHub "
            f"(Settings → Secrets and variables → Actions)."
        )
    return valor


def ejecutar(cmd: list, cwd=None) -> str:
    """Ejecuta un programa y devuelve lo que escribe. Si falla, lanza un error claro."""
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(
            f"Falló el comando {cmd[0]}:\n{(r.stderr or r.stdout)[-1500:]}"
        )
    return r.stdout


def duracion_video(ruta) -> float:
    salida = ejecutar([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "json", str(ruta),
    ])
    return float(json.loads(salida)["format"]["duration"])


def formato_tiempo(seg: float) -> str:
    seg = int(seg)
    h, resto = divmod(seg, 3600)
    m, s = divmod(resto, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
