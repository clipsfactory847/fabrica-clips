"""PROGRAMA PRINCIPAL DE LA FÁBRICA DE CLIPS.
Busca los clips de Twitch que más se están viendo → la IA elige los que más enganchan →
los edita en vertical con subtítulos → te los manda a Telegram para aprobarlos."""
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

from . import analisis, edicion, ia, telegram, transcripcion, twitch, twitch_api
from .estado import Estado
from .telegram import esc
from .util import RAIZ, cargar_config, formato_tiempo, log

SALIDA = RAIZ / "salida"   # copia de seguridad de los clips (se guarda en GitHub unos días)
MARGEN = 1.0


# ----------------------------------------------------------------------
#  ENVIAR UN CLIP TERMINADO
# ----------------------------------------------------------------------
def enviar_clip(final: Path, info: dict, cuenta: dict, clave: str, n: int, total: int, extra: str = "") -> None:
    hashtags = list(dict.fromkeys(list(info.get("hashtags", [])) + cuenta.get("hashtags_fijos", [])))
    pie = (f"🎬 <b>Clip {n} de {total}</b> · nota {info['puntuacion']:.0f}/10\n"
           f"💡 {esc(info.get('motivo', ''))}{extra}")
    telegram.video(final, pie)
    telegram.mensaje(
        "📋 <b>Para copiar al subirlo a YouTube:</b>\n\n"
        f"<b>Título:</b>\n<code>{esc(info.get('titulo', ''))}</code>\n\n"
        f"<b>Descripción:</b>\n<code>{esc(info.get('descripcion', ''))}\n\n{esc(' '.join(hashtags))}</code>",
        botones=telegram.botones_decision(clave),
    )
    SALIDA.mkdir(exist_ok=True)
    shutil.copy(final, SALIDA / f"{clave}.mp4")


# ----------------------------------------------------------------------
#  MODO PRINCIPAL: CLIPS POPULARES DE TWITCH
# ----------------------------------------------------------------------
def buscar_clips_populares(cuenta: dict, estado: Estado) -> list:
    idioma = cuenta.get("idioma", "es")
    horas = int(cuenta.get("horas_atras", 36))
    categorias = twitch_api.ids_de_categorias(cuenta.get("categorias", []))
    nombre_categoria = {v: k for k, v in categorias.items()}
    streamers = twitch_api.ids_de_streamers(cuenta.get("streamers", []))

    todos = {}
    for nombre, gid in categorias.items():
        for c in twitch_api.clips_populares({"game_id": gid}, horas, idioma):
            todos[c["id"]] = c
    for login, uid in streamers.items():
        for c in twitch_api.clips_populares({"broadcaster_id": uid}, horas, idioma):
            todos[c["id"]] = c
    log(f"Encontrados {len(todos)} clips en español de las últimas {horas} h.")

    minimas = int(cuenta.get("vistas_minimas", 300))
    candidatos = [c for c in todos.values()
                  if not estado.clip_visto(c["id"]) and c.get("view_count", 0) >= minimas
                  and float(c.get("duration", 0)) >= cuenta.get("duracion_min", 12)]

    # Quitar clips repetidos del mismo momento (varios espectadores clipean lo mismo)
    candidatos.sort(key=lambda c: c.get("view_count", 0), reverse=True)
    elegidos = []
    for c in candidatos:
        repetido = any(
            c.get("video_id") and c.get("video_id") == e.get("video_id")
            and c.get("vod_offset") is not None and e.get("vod_offset") is not None
            and abs(c["vod_offset"] - e["vod_offset"]) < 45
            for e in elegidos)
        if repetido:
            estado.marcar_clip(c["id"], resultado="repetido")
        else:
            c["categoria"] = nombre_categoria.get(c.get("game_id"), "")
            elegidos.append(c)
    return elegidos[: int(cuenta.get("candidatos_a_revisar", 15))]


def procesar_clips_populares(cuenta: dict, estado: Estado) -> int:
    candidatos = buscar_clips_populares(cuenta, estado)
    if not candidatos:
        log("No hay clips nuevos que superen el mínimo de visualizaciones.")
        return 0

    carpeta = Path(tempfile.mkdtemp(prefix="fabrica_"))
    try:
        preparados = []
        for n, c in enumerate(candidatos, 1):
            log(f"[{n}/{len(candidatos)}] {c['broadcaster_name']}: {c['title']} ({c['view_count']} vistas)")
            try:
                archivo = twitch.descargar_clip(c, carpeta / f"twitch_{n}.mp4")
                duracion = float(c.get("duration", 30))
                t = transcripcion.transcribir_trozo(archivo, 0, duracion, cuenta.get("idioma", "es"), carpeta)
                preparados.append({
                    "id": c["id"], "streamer": c["broadcaster_name"], "categoria": c.get("categoria", ""),
                    "titulo": c["title"], "vistas": c["view_count"], "duracion": duracion,
                    "frases": t["frases"], "palabras": t["palabras"], "archivo": archivo, "datos": c,
                })
            except Exception as e:
                log(f"   ⚠️ Lo salto: {str(e)[:200]}")
                estado.marcar_clip(c["id"], resultado="error")

        if not preparados:
            return 0
        notas = ia.evaluar_clips_twitch(preparados, cuenta)
        por_id = {p["id"]: p for p in preparados}
        for p in preparados:  # apuntamos todos como vistos para no repetirlos
            nota = next((x["puntuacion"] for x in notas if x["id"] == p["id"]), None)
            estado.marcar_clip(p["id"], streamer=p["streamer"], titulo=p["titulo"],
                               vistas=p["vistas"], nota=nota, resultado="revisado")

        maximo = int(cuenta.get("clips_por_ejecucion", 3))
        enviados = 0
        for info in notas[:maximo]:
            p = por_id[info["id"]]
            try:
                rel = [{"t0": w["t0"] - info["inicio"], "t1": w["t1"] - info["inicio"], "w": w["w"]}
                       for w in p["palabras"] if info["inicio"] <= w["t0"] < info["fin"]]
                final = edicion.editar_clip(p["archivo"], carpeta / f"final_{p['id']}.mp4",
                                            info["inicio"], info["fin"] - info["inicio"], rel,
                                            info.get("titulo_pantalla", ""), cuenta.get("estilo", {}))
                login = p["datos"].get("broadcaster_login") or p["streamer"].lower()
                info["descripcion"] = (info.get("descripcion", "") +
                                       f"\n\n🎥 Clip del directo de {p['streamer']}: twitch.tv/{login}")
                extra = (f"\n👀 {p['vistas']} vistas en Twitch · {esc(p['streamer'])}"
                         f"{' · ' + esc(p['categoria']) if p['categoria'] else ''}")
                enviar_clip(final, info, cuenta, p["id"], enviados + 1, min(maximo, len(notas)), extra)
                estado.marcar_clip(p["id"], resultado="enviado")
                enviados += 1
            except Exception as e:
                log(f"Fallo preparando el clip {p['id']}: {e}")
                telegram.mensaje(f"⚠️ No pude preparar un clip de {esc(p['streamer'])}: {esc(str(e)[:300])}")
        return enviados
    finally:
        shutil.rmtree(carpeta, ignore_errors=True)


# ----------------------------------------------------------------------
#  MODO MANUAL: ANALIZAR UN DIRECTO COMPLETO (cuando pegas un enlace)
# ----------------------------------------------------------------------
def procesar_directo(d: dict, cuenta: dict) -> int:
    streamer = d.get("streamer") or "el streamer"
    carpeta = Path(tempfile.mkdtemp(prefix="fabrica_"))
    telegram.mensaje(f"🔎 Empiezo a analizar un directo de <b>{esc(streamer)}</b>:\n{esc(d['titulo'])}")
    try:
        audio = twitch.descargar_audio(d["enlace"], carpeta)
        candidatos = analisis.momentos_candidatos(analisis.volumen_por_segundo(audio))
        fragmentos = []
        for n, (ini, fin) in enumerate(candidatos, 1):
            log(f"Transcribiendo momento {n}/{len(candidatos)} (minuto {formato_tiempo(ini)})...")
            t = transcripcion.transcribir_trozo(audio, ini, fin, cuenta.get("idioma", "es"), carpeta)
            if t["frases"]:
                fragmentos.append({"inicio": ini, "fin": fin, **t})
        cuenta_directo = dict(cuenta, clips_por_directo=cuenta.get("clips_por_ejecucion", 3))
        clips = ia.elegir_clips(fragmentos, streamer, d["titulo"], cuenta_directo) if fragmentos else []
        palabras = [w for f in fragmentos for w in f["palabras"]]
        enviados = 0
        for n, c in enumerate(clips, 1):
            try:
                bruto = twitch.descargar_trozo(d["enlace"], max(0, c["inicio"] - MARGEN), c["fin"] + MARGEN,
                                               carpeta / f"bruto_{n}.mp4")
                desfase = MARGEN if c["inicio"] >= MARGEN else c["inicio"]
                rel = [{"t0": w["t0"] - c["inicio"], "t1": w["t1"] - c["inicio"], "w": w["w"]}
                       for w in palabras if c["inicio"] <= w["t0"] < c["fin"]]
                final = edicion.editar_clip(bruto, carpeta / f"clip_{n}.mp4", desfase, c["fin"] - c["inicio"],
                                            rel, c.get("titulo_pantalla", ""), cuenta.get("estilo", {}))
                clave = f"{d['id']}_{int(c['inicio'])}"
                enviar_clip(final, c, cuenta, clave, n, len(clips),
                            f"\n⏱ Minuto {formato_tiempo(c['inicio'])} del directo")
                enviados += 1
            except Exception as e:
                telegram.mensaje(f"⚠️ No pude preparar el clip {n}: {esc(str(e)[:300])}")
        return enviados
    finally:
        shutil.rmtree(carpeta, ignore_errors=True)


# ----------------------------------------------------------------------
def main() -> int:
    config = cargar_config()
    estado = Estado()
    telegram.recoger_decisiones(estado)

    cuentas = [c for c in config.get("cuentas", []) if c.get("activa", True)]
    if not cuentas:
        log("No hay cuentas activas en config/cuentas.yaml")
        return 0

    enlace = os.environ.get("ENLACE", "").strip()
    if enlace:
        d = twitch.info_directo(enlace)
        n = procesar_directo(d, cuentas[0])
        estado.marcar(d["id"], d.get("streamer", ""), d["titulo"], n)
        telegram.mensaje(f"✅ Directo analizado. Te he enviado <b>{n}</b> clip(s).")
        return 0

    total = 0
    for cuenta in cuentas:
        log(f"===== Cuenta: {cuenta.get('nombre')} =====")
        total += procesar_clips_populares(cuenta, estado)
    estado.limpiar_antiguos()
    estado.guardar()
    if total:
        telegram.mensaje(f"✅ Ronda terminada: te he enviado <b>{total}</b> clip(s) nuevos. "
                         "Pulsa ✅ o ❌ en cada uno.")
    log(f"Ronda terminada. Clips enviados: {total}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        traceback.print_exc()
        telegram.mensaje(f"❌ <b>Error en la fábrica de clips</b>\n<code>{esc(str(error)[:1500])}</code>")
        sys.exit(1)
