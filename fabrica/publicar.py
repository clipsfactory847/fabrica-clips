"""Publicación: guarda el vídeo en una dirección pública (GitHub Releases)
y lo manda a Buffer, que lo publica en YouTube, TikTok e Instagram."""
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from .util import log

BUFFER_API = "https://api.buffer.com"
CATEGORIA_YOUTUBE = "20"  # 20 = Gaming (Videojuegos)


# ----------------------------------------------------------------------
#  1) Alojar el vídeo en una dirección pública (lo necesita Buffer)
# ----------------------------------------------------------------------
def _gh() -> tuple:
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not token or not repo:
        raise RuntimeError("Falta GITHUB_TOKEN/GITHUB_REPOSITORY (solo funciona dentro de GitHub).")
    return repo, {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}


def _release_clips() -> dict:
    repo, h = _gh()
    r = requests.get(f"https://api.github.com/repos/{repo}/releases/tags/clips", headers=h, timeout=30)
    if r.status_code == 200:
        return r.json()
    r = requests.post(f"https://api.github.com/repos/{repo}/releases", headers=h, json={
        "tag_name": "clips", "name": "Clips para publicar",
        "body": "Vídeos generados automáticamente por la fábrica de clips.",
    }, timeout=30)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"No pude crear el almacén de vídeos en GitHub: {r.text[:300]}")
    return r.json()


def alojar_video(ruta: Path, nombre: str) -> str:
    """Sube el vídeo a GitHub Releases y devuelve su dirección pública."""
    repo, h = _gh()
    rel = _release_clips()
    nombre = "".join(c if c.isalnum() or c in "-_" else "_" for c in nombre)[:80] + ".mp4"
    for a in rel.get("assets", []):  # si ya existe, lo reutilizamos
        if a["name"] == nombre:
            return a["browser_download_url"]
    with open(ruta, "rb") as f:
        r = requests.post(
            f"https://uploads.github.com/repos/{repo}/releases/{rel['id']}/assets",
            params={"name": nombre}, data=f,
            headers={**h, "Content-Type": "video/mp4"}, timeout=600)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"No pude guardar el vídeo en GitHub: {r.text[:300]}")
    _limpiar_antiguos(rel, h, repo)
    return r.json()["browser_download_url"]


def _limpiar_antiguos(rel: dict, h: dict, repo: str, maximo: int = 300) -> None:
    activos = sorted(rel.get("assets", []), key=lambda a: a["created_at"])
    for a in activos[: max(0, len(activos) - maximo)]:
        requests.delete(f"https://api.github.com/repos/{repo}/releases/assets/{a['id']}", headers=h, timeout=30)


# ----------------------------------------------------------------------
#  2) Buffer
# ----------------------------------------------------------------------
def buffer_configurado() -> bool:
    return bool(os.environ.get("BUFFER_API_KEY", "").strip())


def _gql(query: str, variables: dict = None) -> dict:
    r = requests.post(BUFFER_API, json={"query": query, "variables": variables or {}}, headers={
        "Authorization": f"Bearer {os.environ['BUFFER_API_KEY'].strip()}",
        "Content-Type": "application/json",
    }, timeout=120)
    datos = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code != 200 or datos.get("errors"):
        raise RuntimeError(f"Error de Buffer ({r.status_code}): {str(datos.get('errors') or r.text)[:400]}")
    return datos["data"]


def canales_buffer() -> list:
    orgs = _gql("query { account { organizations { id name } } }")["account"]["organizations"]
    canales = []
    for o in orgs:
        datos = _gql("query($input: ChannelsInput!) { channels(input: $input) { id name service } }",
                     {"input": {"organizationId": o["id"]}})
        canales.extend(datos["channels"])
    return canales


CREAR = """mutation($input: CreatePostInput!) {
  createPost(input: $input) {
    ... on PostActionSuccess { post { id dueAt } }
    ... on MutationError { message }
  }
}"""


def _crear_post(entrada: dict) -> str:
    res = _gql(CREAR, {"input": entrada})["createPost"]
    if res.get("message"):
        raise RuntimeError(res["message"])
    return res["post"].get("dueAt") or "ahora"


MADRID = ZoneInfo("Europe/Madrid")


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _leer(x: str) -> datetime:
    return datetime.fromisoformat(x.replace("Z", "+00:00"))


def hueco_youtube(registro: list, config_pub: dict, ahora: datetime = None) -> datetime:
    """Busca la próxima hora buena para YouTube respetando el límite de subidas de YouTube
    (máximo N vídeos en cualquier periodo de 24 h, con margen) y dejando horas entre vídeos."""
    ahora = ahora or datetime.now(timezone.utc)
    horas = config_pub.get("horas_youtube", ["13:30", "21:30"])
    maximo = int(config_pub.get("youtube_max_24h", 2))
    ventana = timedelta(hours=24, minutes=45)
    separacion = timedelta(hours=float(config_pub.get("horas_entre_videos", 5)))
    ocupados = sorted(_leer(x) for x in registro)

    def cabe(t: datetime) -> bool:
        todos = sorted(ocupados + [t])
        if any(abs(t - o) < separacion for o in ocupados):
            return False
        return all(sum(1 for y in todos if x - ventana < y <= x) <= maximo for x in todos)

    hoy = ahora.astimezone(MADRID).date()
    for dia in range(0, 30):
        fecha = hoy + timedelta(days=dia)
        for h in horas:
            hh, mm = (int(v) for v in h.split(":"))
            base = datetime(fecha.year, fecha.month, fecha.day, hh, mm, tzinfo=MADRID)
            # si la hora justa no cabe, probamos a retrasarla un poco (hasta 2 h)
            for extra in range(0, 121, 30):
                t = (base + timedelta(minutes=extra)).astimezone(timezone.utc)
                if t >= ahora + timedelta(minutes=15) and cabe(t):
                    return t
    return ahora + timedelta(days=1)


def publicar_en_buffer(info: dict, config_pub: dict, registro: list = None, solo: list = None) -> list:
    """info = {'titulo','descripcion','hashtags','video_url'}. Devuelve líneas de resultado.
    registro: lista (se modifica) con las horas ya programadas en YouTube, para no pasar del límite.
    solo: limitar a ciertas redes, p. ej. ["youtube"]."""
    titulo = info.get("titulo", "")[:100]
    hashtags = " ".join(info.get("hashtags", []))
    descripcion = info.get("descripcion", "")
    avisos = set(config_pub.get("con_aviso", ["tiktok"]))  # redes donde terminas tú con un toque
    registro = registro if registro is not None else []

    canales = [c for c in canales_buffer() if c["service"].lower() in ("youtube", "tiktok", "instagram")
               and (not solo or c["service"].lower() in solo)]
    # Una sola hora para todas las redes, elegida para que YouTube nunca pase de su límite diario
    cuando = hueco_youtube(registro, config_pub)

    resultados = []
    for c in canales:
        servicio = c["service"].lower()
        entrada = {
            "channelId": c["id"],
            "schedulingType": "notification" if servicio in avisos else "automatic",
            "mode": "customScheduled",
            "dueAt": _iso(cuando),
            "assets": [{"video": {"url": info["video_url"], "metadata": {"thumbnailOffset": 1000}}}],
        }
        if servicio == "youtube":
            entrada["text"] = f"{descripcion}\n\n{hashtags}"
            entrada["metadata"] = {"youtube": {"title": titulo, "categoryId": CATEGORIA_YOUTUBE,
                                               "privacy": "public", "madeForKids": False}}
        elif servicio == "instagram":
            entrada["text"] = f"{titulo}\n\n{descripcion}\n\n{hashtags}"
            entrada["metadata"] = {"instagram": {"type": "reel", "shouldShareToFeed": True}}
        else:  # tiktok
            entrada["text"] = f"{titulo}\n\n{descripcion}\n\n{hashtags}"[:2200]

        nombre = {"youtube": "YouTube", "tiktok": "TikTok", "instagram": "Instagram"}[servicio]
        hora = cuando.astimezone(MADRID).strftime("%d/%m a las %H:%M")
        try:
            _crear_post(entrada)
            if servicio == "youtube":
                registro.append(_iso(cuando))
            if entrada["schedulingType"] == "notification":
                resultados.append(f"📲 {nombre}: el {hora} te llegará un aviso de Buffer para terminarlo")
            else:
                resultados.append(f"🗓 {nombre}: se publicará solo el {hora}")
        except Exception as e:
            resultados.append(f"❌ {nombre}: {str(e)[:150]}")
    if not resultados:
        resultados.append("⚠️ No hay redes conectadas en Buffer todavía.")
    # guardamos solo lo de los últimos días
    limite = datetime.now(timezone.utc) - timedelta(days=3)
    registro[:] = [x for x in registro if _leer(x) > limite]
    return resultados
