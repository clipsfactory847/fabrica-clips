"""Publicación: guarda el vídeo en una dirección pública (GitHub Releases)
y lo manda a Buffer, que lo publica en YouTube, TikTok e Instagram."""
import os
from pathlib import Path

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


def publicar_en_buffer(info: dict, config_pub: dict) -> list:
    """info = {'titulo','descripcion','hashtags','video_url'}. Devuelve líneas de resultado."""
    titulo = info.get("titulo", "")[:100]
    hashtags = " ".join(info.get("hashtags", []))
    descripcion = info.get("descripcion", "")
    modo = config_pub.get("modo", "addToQueue")          # addToQueue = según tu horario de Buffer
    avisos = set(config_pub.get("con_aviso", ["tiktok"]))  # redes donde terminas tú con un toque

    resultados = []
    for c in canales_buffer():
        servicio = c["service"].lower()
        if servicio not in ("youtube", "tiktok", "instagram"):
            continue
        entrada = {
            "channelId": c["id"],
            "schedulingType": "notification" if servicio in avisos else "automatic",
            "mode": modo,
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
        try:
            try:
                cuando = _crear_post(entrada)
            except RuntimeError as e:
                # Si la cola gratuita está llena o falla el modo, lo intentamos publicar ya
                if modo != "shareNow":
                    log(f"{nombre}: {e} → pruebo a publicar ahora")
                    entrada["mode"] = "shareNow"
                    cuando = _crear_post(entrada)
                else:
                    raise
            if entrada["schedulingType"] == "notification":
                resultados.append(f"📲 {nombre}: te llegará un aviso de Buffer para terminarlo con un toque")
            elif entrada["mode"] == "shareNow":
                resultados.append(f"✅ {nombre}: publicándose ahora")
            else:
                resultados.append(f"🗓 {nombre}: programado ({cuando[:16].replace('T', ' ')} UTC)")
        except Exception as e:
            resultados.append(f"❌ {nombre}: {str(e)[:150]}")
    if not resultados:
        resultados.append("⚠️ No hay redes conectadas en Buffer todavía.")
    return resultados
