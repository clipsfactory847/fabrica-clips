"""Conexión oficial con Twitch (API Helix): buscar clips populares, directos y categorías."""
import time
from datetime import datetime, timedelta, timezone

import requests

from .util import log, secreto

_token = {"valor": None, "caduca": 0}
API = "https://api.twitch.tv/helix"


def _cabeceras() -> dict:
    if not _token["valor"] or time.time() > _token["caduca"] - 60:
        r = requests.post("https://id.twitch.tv/oauth2/token", params={
            "client_id": secreto("TWITCH_CLIENT_ID"),
            "client_secret": secreto("TWITCH_CLIENT_SECRET"),
            "grant_type": "client_credentials",
        }, timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f"Twitch no aceptó las claves (TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET): {r.text[:300]}")
        datos = r.json()
        _token["valor"] = datos["access_token"]
        _token["caduca"] = time.time() + datos.get("expires_in", 3600)
    return {"Client-Id": secreto("TWITCH_CLIENT_ID"), "Authorization": f"Bearer {_token['valor']}"}


def _get(ruta: str, params) -> list:
    r = requests.get(f"{API}/{ruta}", headers=_cabeceras(), params=params, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"Error de Twitch en {ruta} ({r.status_code}): {r.text[:300]}")
    return r.json().get("data", [])


def ids_de_categorias(nombres: list) -> dict:
    if not nombres:
        return {}
    datos = _get("games", [("name", n) for n in nombres])
    encontrados = {g["name"].lower(): g["id"] for g in datos}
    for n in nombres:
        if n.lower() not in encontrados:
            log(f"⚠️ No encuentro la categoría '{n}' en Twitch (revisa el nombre exacto).")
    return {g["name"]: g["id"] for g in datos}


def ids_de_streamers(usuarios: list) -> dict:
    if not usuarios:
        return {}
    datos = _get("users", [("login", u.lower()) for u in usuarios])
    return {u["login"]: u["id"] for u in datos}


def clips_populares(filtro: dict, horas: int, idioma: str, cuantos: int = 100) -> list:
    """filtro = {'game_id': ...} o {'broadcaster_id': ...}"""
    ahora = datetime.now(timezone.utc)
    params = dict(filtro)
    params.update({
        "started_at": (ahora - timedelta(hours=horas)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ended_at": ahora.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "first": min(cuantos, 100),
    })
    clips = _get("clips", params)
    if idioma:
        clips = [c for c in clips if (c.get("language") or "").lower().startswith(idioma)]
    return clips


def directos_guardados(user_id: str, cuantos: int = 5) -> list:
    return _get("videos", {"user_id": user_id, "type": "archive", "first": cuantos})


def esta_en_directo(user_id: str) -> bool:
    return bool(_get("streams", {"user_id": user_id}))
