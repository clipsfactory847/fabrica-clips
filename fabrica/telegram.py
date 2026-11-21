"""Envía mensajes y vídeos a tu Telegram."""
import html
from pathlib import Path

import requests

from .util import log, secreto


def _api(metodo: str) -> str:
    return f"https://api.telegram.org/bot{secreto('TELEGRAM_TOKEN')}/{metodo}"


def mensaje(texto: str) -> None:
    try:
        r = requests.post(_api("sendMessage"), data={
            "chat_id": secreto("TELEGRAM_CHAT_ID"), "text": texto[:4000],
            "parse_mode": "HTML", "disable_web_page_preview": "true",
        }, timeout=60)
        if r.status_code != 200:
            log(f"Telegram no aceptó el mensaje: {r.text[:300]}")
    except Exception as e:  # un fallo de aviso nunca debe parar el sistema
        log(f"No se pudo avisar por Telegram: {e}")


def video(ruta: Path, pie: str) -> None:
    with open(ruta, "rb") as f:
        r = requests.post(_api("sendVideo"), data={
            "chat_id": secreto("TELEGRAM_CHAT_ID"), "caption": pie[:1000],
            "parse_mode": "HTML", "supports_streaming": "true",
            "width": 1080, "height": 1920,
        }, files={"video": (ruta.name, f, "video/mp4")}, timeout=600)
    if r.status_code != 200:
        raise RuntimeError(f"Telegram no aceptó el vídeo: {r.text[:300]}")


def esc(texto) -> str:
    return html.escape(str(texto))
