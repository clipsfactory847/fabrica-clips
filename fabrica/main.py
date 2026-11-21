"""PROGRAMA PRINCIPAL DE LA FÁBRICA DE CLIPS.
Busca directos nuevos → encuentra los mejores momentos → edita los clips → te los manda a Telegram."""
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

from . import analisis, edicion, ia, telegram, transcripcion, twitch
from .estado import Estado
from .telegram import esc
from .util import cargar_config, formato_tiempo, log

MARGEN = 1.0  # segundos extra que descargamos antes de cada clip


def buscar_trabajo(cuenta: dict, estado: Estado) -> list:
    """Devuelve la lista de directos nuevos que hay que procesar."""
    enlace = os.environ.get("ENLACE", "").strip()
    if enlace:
        info = twitch.info_directo(enlace)
        log(f"Procesando el directo indicado a mano: {info['titulo']}")
        return [info]

    trabajos = []
    for s in cuenta.get("streamers", []):
        if s.get("plataforma") != "twitch":
            log(f"Plataforma {s.get('plataforma')} aún no soportada, salto a {s.get('usuario')}.")
            continue
        usuario = s["usuario"]
        directos = twitch.listar_directos(usuario)
        if directos and twitch.esta_en_directo(usuario):
            log(f"{usuario} está en directo ahora: espero a que termine para procesar el último.")
            directos = directos[1:]
        for d in directos:
            if not estado.ya_procesado(d["id"]):
                d["streamer"] = usuario
                trabajos.append(d)
                break  # el más reciente sin procesar de cada streamer
    return trabajos


def procesar_directo(d: dict, cuenta: dict, estado: Estado) -> int:
    streamer = d.get("streamer") or "el streamer"
    carpeta = Path(tempfile.mkdtemp(prefix="fabrica_"))
    telegram.mensaje(f"🔎 Empiezo a analizar un directo de <b>{esc(streamer)}</b>:\n{esc(d['titulo'])}")

    try:
        # 1) Audio completo del directo
        audio = twitch.descargar_audio(d["enlace"], carpeta)

        # 2) Momentos donde "pasa algo" según el volumen
        volumen = analisis.volumen_por_segundo(audio)
        candidatos = analisis.momentos_candidatos(volumen)
        if not candidatos:
            telegram.mensaje("😐 No he encontrado momentos interesantes en este directo.")
            return 0

        # 3) Transcribir solo esos momentos
        fragmentos = []
        for n, (ini, fin) in enumerate(candidatos, 1):
            log(f"Transcribiendo momento {n}/{len(candidatos)} (minuto {formato_tiempo(ini)})...")
            t = transcripcion.transcribir_trozo(audio, ini, fin, cuenta.get("idioma", "es"), carpeta)
            if t["frases"]:
                fragmentos.append({"inicio": ini, "fin": fin, **t})

        # 4) La IA elige los mejores clips
        clips = ia.elegir_clips(fragmentos, streamer, d["titulo"], cuenta)
        if not clips:
            telegram.mensaje("😐 La IA no ha visto ningún momento con suficiente calidad en este directo.")
            return 0

        # 5) Descargar, editar y enviar cada clip
        palabras = [w for f in fragmentos for w in f["palabras"]]
        enviados = 0
        for n, c in enumerate(clips, 1):
            try:
                log(f"Clip {n}: {c.get('titulo')} ({formato_tiempo(c['inicio'])} - {formato_tiempo(c['fin'])})")
                bruto = twitch.descargar_trozo(
                    d["enlace"], max(0, c["inicio"] - MARGEN), c["fin"] + MARGEN,
                    carpeta / f"bruto_{n}.mp4")
                desfase = MARGEN if c["inicio"] >= MARGEN else c["inicio"]
                rel = [{"t0": w["t0"] - c["inicio"], "t1": w["t1"] - c["inicio"], "w": w["w"]}
                       for w in palabras if c["inicio"] <= w["t0"] < c["fin"]]
                final = edicion.editar_clip(
                    bruto, carpeta / f"clip_{n}.mp4", desfase, c["fin"] - c["inicio"],
                    rel, c.get("titulo_pantalla", ""), cuenta.get("estilo", {}))

                hashtags = list(dict.fromkeys(list(c.get("hashtags", [])) + cuenta.get("hashtags_fijos", [])))
                pie = (f"🎬 <b>Clip {n} de {len(clips)}</b> · nota {c['puntuacion']:.0f}/10\n"
                       f"💡 {esc(c.get('motivo', ''))}\n"
                       f"⏱ Minuto {formato_tiempo(c['inicio'])} del directo")
                telegram.video(final, pie)
                telegram.mensaje(
                    "📋 <b>Para copiar al subirlo a YouTube:</b>\n\n"
                    f"<b>Título:</b>\n<code>{esc(c.get('titulo', ''))}</code>\n\n"
                    f"<b>Descripción:</b>\n<code>{esc(c.get('descripcion', ''))}\n\n{esc(' '.join(hashtags))}</code>")
                enviados += 1
            except Exception as e:
                log(f"Fallo en el clip {n}: {e}")
                telegram.mensaje(f"⚠️ No pude preparar el clip {n}: {esc(str(e)[:300])}")
        return enviados
    finally:
        shutil.rmtree(carpeta, ignore_errors=True)


def main() -> int:
    config = cargar_config()
    estado = Estado()
    cuentas = [c for c in config.get("cuentas", []) if c.get("activa", True)]
    if not cuentas:
        log("No hay cuentas activas en config/cuentas.yaml")
        return 0
    cuenta = cuentas[0]  # Fase 3: una sola cuenta

    trabajos = buscar_trabajo(cuenta, estado)
    if not trabajos:
        log("No hay directos nuevos. Nada que hacer.")
        return 0

    for d in trabajos:
        enviados = procesar_directo(d, cuenta, estado)
        estado.marcar(d["id"], d.get("streamer", ""), d["titulo"], enviados)
        telegram.mensaje(f"✅ Terminado. Te he enviado <b>{enviados}</b> clip(s) de este directo.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        traceback.print_exc()
        telegram.mensaje(f"❌ <b>Error en la fábrica de clips</b>\n<code>{esc(str(error)[:1500])}</code>")
        sys.exit(1)
