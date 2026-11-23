"""PROGRAMA PRINCIPAL DE LA FÁBRICA DE CLIPS.
Busca los clips de Twitch que más se están viendo → la IA elige los que más enganchan →
los edita en vertical con subtítulos → te los manda a Telegram para aprobarlos."""
import os
import shutil
from datetime import datetime, timezone
import sys
import tempfile
import traceback
from pathlib import Path

from . import analisis, edicion, ia, publicar, telegram, transcripcion, twitch, twitch_api
from .estado import Estado
from .telegram import esc
from .util import RAIZ, cargar_config, formato_tiempo, log

SALIDA = RAIZ / "salida"   # copia de seguridad de los clips (se guarda en GitHub unos días)
MARGEN = 1.0


# ----------------------------------------------------------------------
#  ENVIAR UN CLIP TERMINADO
# ----------------------------------------------------------------------
def enviar_clip(final: Path, info: dict, cuenta: dict, clave: str, n: int, total: int,
                extra: str = "", estado: Estado = None) -> None:
    hashtags = list(dict.fromkeys(list(info.get("hashtags", [])) + cuenta.get("hashtags_fijos", [])))
    # Guardamos el vídeo en una dirección pública para poder publicarlo al aprobarlo
    if estado is not None:
        try:
            url = publicar.alojar_video(final, clave)
            estado.marcar_clip(clave, publicar={
                "titulo": info.get("titulo", ""), "descripcion": info.get("descripcion", ""),
                "hashtags": hashtags, "video_url": url})
        except Exception as e:
            log(f"No pude preparar el clip para publicar: {e}")
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
def _horas_desde(c: dict) -> float:
    try:
        creado = datetime.strptime(c["created_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return max(1.0, (datetime.now(timezone.utc) - creado).total_seconds() / 3600)
    except Exception:
        return 24.0


def _viralidad(c: dict) -> float:
    """Visualizaciones por hora desde que se creó el clip: mide lo rápido que se está haciendo viral."""
    return c.get("view_count", 0) / _horas_desde(c)


def resumen_tendencias(clips: list, cuantos: int = 20) -> str:
    """Texto con los clips más virales de las últimas horas, para que la IA sepa qué está funcionando."""
    top = sorted(clips, key=_viralidad, reverse=True)[:cuantos]
    if not top:
        return "(no hay datos de tendencias)"
    lineas = [f"- {c['broadcaster_name']} | {c.get('categoria') or c.get('game_name') or '?'} | "
              f"\"{c['title']}\" | {c['view_count']} vistas en {_horas_desde(c):.0f} h" for c in top]
    juegos, streamers = {}, {}
    for c in top:
        j = c.get("categoria") or c.get("game_name") or ""
        if j:
            juegos[j] = juegos.get(j, 0) + 1
        streamers[c["broadcaster_name"]] = streamers.get(c["broadcaster_name"], 0) + 1
    lineas.append("Juegos/categorías que más se repiten: " +
                  ", ".join(f"{k} ({v})" for k, v in sorted(juegos.items(), key=lambda x: -x[1])[:6]))
    lineas.append("Streamers más virales ahora: " +
                  ", ".join(f"{k} ({v})" for k, v in sorted(streamers.items(), key=lambda x: -x[1])[:8]))
    return "\n".join(lineas)


def buscar_clips_populares(cuenta: dict, estado: Estado) -> tuple:
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

    # TENDENCIAS: los streamers en español con más espectadores ahora mismo (grandes o pequeños que estén petándolo)
    juego_de = {}
    if cuenta.get("seguir_tendencias", True):
        try:
            vivos = twitch_api.directos_en_vivo(idioma, int(cuenta.get("directos_tendencia", 60)))
            ya = set(streamers.values())
            nuevos = 0
            for d in vivos:
                juego_de[d["user_id"]] = d.get("game_name", "")
                if d["user_id"] in ya:
                    continue
                for c in twitch_api.clips_populares({"broadcaster_id": d["user_id"]}, horas, idioma):
                    todos.setdefault(c["id"], c)
                    nuevos += 1
            log(f"Tendencias: revisados {len(vivos)} directos en vivo en español ({nuevos} clips suyos).")
        except Exception as e:
            log(f"⚠️ No pude mirar las tendencias: {str(e)[:200]}")
    for c in todos.values():
        c["categoria"] = nombre_categoria.get(c.get("game_id")) or juego_de.get(c.get("broadcaster_id"), "")
    log(f"Encontrados {len(todos)} clips en español de las últimas {horas} h.")

    recientes = [c for c in todos.values() if _horas_desde(c) <= int(cuenta.get("horas_tendencia", 12))]
    tendencias = resumen_tendencias(recientes or list(todos.values()))
    log("Lo más viral ahora:\n" + tendencias)

    minimas = int(cuenta.get("vistas_minimas", 300))
    candidatos = [c for c in todos.values()
                  if not estado.clip_visto(c["id"]) and c.get("view_count", 0) >= minimas
                  and float(c.get("duration", 0)) >= cuenta.get("duracion_min", 12)]

    # Primero los que se están haciendo virales MÁS RÁPIDO (vistas por hora), no solo los que más vistas tienen
    candidatos.sort(key=_viralidad, reverse=True)
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
            elegidos.append(c)
    # Prioridad a los juegos más vistos (Fortnite y GTA), sin dejar fuera lo más viral del resto
    total = int(cuenta.get("candidatos_a_revisar", 15))
    prioritarios = [j.lower() for j in cuenta.get("juegos_prioritarios", [])]
    if prioritarios:
        es_prio = lambda c: any(j in (c.get("categoria") or "").lower() for j in prioritarios)
        cupo = int(total * float(cuenta.get("cuota_prioritarios", 0.7)))
        prio = [c for c in elegidos if es_prio(c)][:cupo]
        resto = [c for c in elegidos if c not in prio][: total - len(prio)]
        log(f"Revisaré {len(prio)} clips de {', '.join(cuenta.get('juegos_prioritarios'))} y {len(resto)} de otros.")
        elegidos = sorted(prio + resto, key=_viralidad, reverse=True)
    return elegidos[:total], tendencias


def elegir_variados(notas: list, por_id: dict, cuenta: dict, maximo: int) -> list:
    """De los clips aprobados por la IA (ya ordenados por nota) elige una mezcla variada:
    como mucho N por streamer y ~70% humor, el resto de otros estilos (jugadas, sustos...)."""
    max_streamer = int(cuenta.get("max_por_streamer", 2))
    max_humor = max(1, round(maximo * float(cuenta.get("porcentaje_humor", 0.7))))
    max_otro = max(1, maximo - max_humor)
    elegidos, por_streamer, humor, otros = [], {}, 0, 0
    for info in notas:
        st = por_id[info["id"]]["streamer"].lower()
        es_humor = str(info.get("estilo", "humor")).lower().startswith("humor")
        if por_streamer.get(st, 0) >= max_streamer:
            continue
        if (es_humor and humor >= max_humor) or (not es_humor and otros >= max_otro):
            continue
        elegidos.append(info)
        por_streamer[st] = por_streamer.get(st, 0) + 1
        humor += es_humor
        otros += not es_humor
        if len(elegidos) >= maximo:
            return elegidos


def procesar_clips_populares(cuenta: dict, estado: Estado) -> int:
    candidatos, tendencias = buscar_clips_populares(cuenta, estado)
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
                    "por_hora": round(_viralidad(c)),
                    "frases": t["frases"], "palabras": t["palabras"], "archivo": archivo, "datos": c,
                })
            except Exception as e:
                log(f"   ⚠️ Lo salto: {str(e)[:200]}")
                estado.marcar_clip(c["id"], resultado="error")

        if not preparados:
            return 0
        try:
            notas = ia.evaluar_clips_twitch(preparados, cuenta, tendencias)
            estado.datos["fallos_gemini"] = 0
        except ia.GeminiSaturado as e:
            # No marcamos los clips como vistos: se volverán a valorar en la siguiente ronda.
            fallos = estado.datos.get("fallos_gemini", 0) + 1
            estado.datos["fallos_gemini"] = fallos
            log(f"{e}. Lo dejo para la siguiente ronda (fallos seguidos: {fallos}).")
            if fallos == 3:
                telegram.mensaje("⚠️ La IA de Google (Gemini) lleva 3 rondas saturada. No hace falta que hagas nada: "
                                 "seguiré intentándolo en cada ronda y te aviso si no se arregla.")
            return 0
        por_id = {p["id"]: p for p in preparados}
        for p in preparados:  # apuntamos todos como vistos para no repetirlos
            nota = next((x["puntuacion"] for x in notas if x["id"] == p["id"]), None)
            estado.marcar_clip(p["id"], streamer=p["streamer"], titulo=p["titulo"],
                               vistas=p["vistas"], nota=nota, resultado="revisado")

        maximo = int(cuenta.get("clips_por_ejecucion", 3))
        if not notas and ia.ULTIMO_MEJOR and ia.ULTIMO_MEJOR["puntuacion"] >= cuenta.get("nota_rescate", 6):
            log(f"Ninguno llega a la nota mínima; te mando el mejor de la ronda ({ia.ULTIMO_MEJOR['puntuacion']}/10).")
            notas = [ia.ULTIMO_MEJOR]
        enviados = 0
        notas = elegir_variados(notas, por_id, cuenta, maximo)
        for info in notas:
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
                enviar_clip(final, info, cuenta, p["id"], enviados + 1, min(maximo, len(notas)), extra, estado)
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
def procesar_directo(d: dict, cuenta: dict, estado: Estado) -> int:
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
                            f"\n⏱ Minuto {formato_tiempo(c['inicio'])} del directo", estado)
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

    cuentas = [c for c in config.get("cuentas", []) if c.get("activa", True)]
    if not cuentas:
        log("No hay cuentas activas en config/cuentas.yaml")
        return 0

    enlace = os.environ.get("ENLACE", "").strip()
    if enlace:
        d = twitch.info_directo(enlace)
        n = procesar_directo(d, cuentas[0], estado)
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
