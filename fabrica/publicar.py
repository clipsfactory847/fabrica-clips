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


def hueco_youtube(registro: list, config_pub: dict, ahora: datetime = None, dias_llenos: set = None) -> datetime:
    """Busca la próxima hora buena para YouTube. Reglas (las de Buffer gratis y YouTube):
    - como mucho N vídeos en el mismo día (hora de Madrid),
    - como mucho N vídeos en cualquier periodo de 24 h (con margen),
    - al menos X horas entre un vídeo y otro.
    dias_llenos: días que Buffer ha dicho que ya están completos (se saltan)."""
    ahora = ahora or datetime.now(timezone.utc)
    horas = config_pub.get("horas_youtube", ["13:30", "21:30"])
    maximo = int(config_pub.get("youtube_max_24h", 2))
    ventana = timedelta(hours=24, minutes=int(config_pub.get("margen_minutos", 15)))
    separacion = timedelta(hours=float(config_pub.get("horas_entre_videos", 5)))
    ocupados = sorted(_leer(x) for x in registro)
    dias_llenos = dias_llenos or set()
    solo_por_dia = bool(config_pub.get("solo_limite_por_dia", False))
    lh, lm = (int(v) for v in str(config_pub.get("hora_limite", "23:00")).split(":"))

    def dia(t: datetime):
        return t.astimezone(MADRID).date()

    def cabe(t: datetime) -> bool:
        if dia(t) in dias_llenos:
            return False
        if sum(1 for o in ocupados if dia(o) == dia(t)) >= maximo:
            return False
        if any(abs(t - o) < separacion for o in ocupados):
            return False
        if solo_por_dia:
            return True
        todos = sorted(ocupados + [t])
        # solo miramos los periodos de 24 h que incluyen la hora nueva
        return all(sum(1 for y in todos if x - ventana < y <= x) <= maximo
                   for x in todos if t <= x < t + ventana)

    hoy = dia(ahora)
    for d in range(0, 60):
        fecha = hoy + timedelta(days=d)
        # Instagram/TikTok: primero las horas exactas y luego retrasos.
        # YouTube: cada hora con sus retrasos (así se reparte mejor con la regla de las 24 h).
        if solo_por_dia:
            orden = [(extra, h) for extra in range(0, 181, 10) for h in horas]
        else:
            orden = [(extra, h) for h in horas for extra in range(0, 181, 10)]
        for extra, h in orden:
            if True:
                hh, mm = (int(v) for v in h.split(":"))
                local = datetime(fecha.year, fecha.month, fecha.day, hh, mm, tzinfo=MADRID) + timedelta(minutes=extra)
                if local.date() != fecha or (local.hour, local.minute) > (lh, lm):
                    continue
                t = local.astimezone(timezone.utc)
                if t >= ahora + timedelta(minutes=15) and cabe(t):
                    return t
    raise RuntimeError("No encuentro ningún hueco libre para YouTube en los próximos 60 días")


def _entrada(servicio: str, canal_id: str, info: dict, cuando: datetime, avisos: set) -> dict:
    titulo = info.get("titulo", "")[:100]
    hashtags = " ".join(info.get("hashtags", []))
    descripcion = info.get("descripcion", "")
    entrada = {
        "channelId": canal_id,
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
    return entrada


NOMBRES = {"youtube": "YouTube", "tiktok": "TikTok", "instagram": "Instagram"}


def config_otras(config_pub: dict) -> dict:
    """Las mismas reglas de calendario, pero con los valores de Instagram/TikTok."""
    c = dict(config_pub)
    c["horas_youtube"] = config_pub.get("horas_otras", config_pub.get("horas_youtube", ["13:30", "21:30"]))
    c["youtube_max_24h"] = config_pub.get("otras_max_24h", config_pub.get("youtube_max_24h", 2))
    c["horas_entre_videos"] = config_pub.get("otras_horas_entre", config_pub.get("horas_entre_videos", 3))
    c["solo_limite_por_dia"] = True  # Instagram/TikTok: basta con no pasar de N por día
    return c


def _es_limite_diario(error: str) -> bool:
    e = error.lower()
    return "maximum number of posts" in e or "daily posting limit" in e or "in a day" in e


def publicar_en_buffer(info: dict, config_pub: dict, registro: list = None, solo: list = None,
                       canales: list = None, registro_otras: list = None) -> dict:
    """Programa el clip en Buffer. Devuelve {"lineas": [...], "fallidas": [redes que fallaron]}.
    registro: lista (se modifica) con las horas ya programadas en YouTube, para no pasar del límite.
    solo: limitar a ciertas redes, p. ej. ["youtube"]."""
    avisos = set(config_pub.get("con_aviso", ["tiktok"]))  # redes donde terminas tú con un toque
    registro = registro if registro is not None else []
    canales = canales if canales is not None else canales_buffer()
    canales = [c for c in canales if c["service"].lower() in NOMBRES
               and (not solo or c["service"].lower() in solo)]
    if not canales:
        return {"lineas": ["⚠️ No hay redes conectadas en Buffer todavía."], "fallidas": list(solo or [])}

    lineas, fallidas = [], []
    dias_llenos = set()
    cuando = None
    # 1) YouTube primero: es la red con límite. Si Buffer dice que ese día está lleno, probamos el siguiente hueco.
    yt = [c for c in canales if c["service"].lower() == "youtube"]
    for c in yt:
        error = ""
        for _ in range(10):
            cuando = hueco_youtube(registro, config_pub, dias_llenos=dias_llenos)
            try:
                _crear_post(_entrada("youtube", c["id"], info, cuando, avisos))
                registro.append(_iso(cuando))
                lineas.append(f"🗓 YouTube: se publicará solo el {cuando.astimezone(MADRID).strftime('%d/%m a las %H:%M')}")
                error = ""
                break
            except Exception as e:
                error = str(e)
                if _es_limite_diario(error):
                    dias_llenos.add(cuando.astimezone(MADRID).date())
                    continue
                break
        if error:
            lineas.append(f"❌ YouTube: {error[:150]}")
            fallidas.append("youtube")
            cuando = None

    # 2) Instagram y TikTok van con su propio calendario (pueden publicar más al día que YouTube,
    #    que en Buffer gratis está limitado a 2). Las dos salen a la misma hora.
    otras = [c for c in canales if c["service"].lower() != "youtube"]
    if not otras:
        cuando = None
    else:
        propio = registro_otras if registro_otras is not None else []
        cuando = hueco_youtube(propio, config_otras(config_pub))
        hora = cuando.astimezone(MADRID).strftime("%d/%m a las %H:%M")
        alguna_bien = False
        for c in otras:
            servicio = c["service"].lower()
            entrada = _entrada(servicio, c["id"], info, cuando, avisos)
            try:
                _crear_post(entrada)
                alguna_bien = True
                if entrada["schedulingType"] == "notification":
                    lineas.append(f"📲 {NOMBRES[servicio]}: el {hora} te llegará un aviso de Buffer para terminarlo")
                else:
                    lineas.append(f"🗓 {NOMBRES[servicio]}: se publicará solo el {hora}")
            except Exception as e:
                lineas.append(f"❌ {NOMBRES[servicio]}: {str(e)[:150]}")
                fallidas.append(servicio)
        if alguna_bien and registro_otras is not None:
            registro_otras.append(_iso(cuando))

    # guardamos solo lo de los últimos días (las horas futuras se quedan)
    limite = datetime.now(timezone.utc) - timedelta(days=3)
    registro[:] = sorted(x for x in registro if _leer(x) > limite)
    if registro_otras is not None:
        registro_otras[:] = sorted(set(x for x in registro_otras if _leer(x) > limite))
    return {"lineas": lineas, "fallidas": fallidas}
