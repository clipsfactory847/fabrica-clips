"""Se ejecuta cada pocos minutos: lee los botones que has pulsado en Telegram
y publica los clips aprobados."""
import sys
import traceback

from . import panel, publicar, telegram
from .estado import Decisiones, Estado
from .telegram import esc
from .util import cargar_config, log


def _registro(decisiones: Decisiones, config: dict) -> list:
    return decisiones.datos.setdefault("youtube_programados", list(config.get("youtube_ya_subidos", [])))


def _pendientes(decisiones: Decisiones) -> dict:
    """Clips aprobados que aún no se han podido programar en alguna red: {clave: [redes]}."""
    return decisiones.datos.setdefault("pendientes", {})


def al_aprobar(clave: str, estado: Estado, decisiones: Decisiones) -> str:
    clip = estado.datos["clips"].get(clave, {})
    info = clip.get("publicar")
    if not info or not info.get("video_url"):
        return "Apuntado. (Este clip es anterior al sistema de publicación: súbelo a mano con el texto de arriba.)"
    if decisiones.datos["clips"].get(clave, {}).get("publicado"):
        return "Este clip ya se había enviado a publicar."
    if not publicar.buffer_configurado():
        return "Apuntado. (La publicación automática aún no está conectada: súbelo a mano con el texto de arriba.)"
    config = cargar_config().get("publicacion", {})
    try:
        res = publicar.publicar_en_buffer(info, config, _registro(decisiones, config),
                                          registro_otras=decisiones.datos.setdefault("otras_programadas", []))
    except Exception as e:
        # Buffer o internet fallaron: lo dejamos pendiente y el bot lo reintenta solo
        _pendientes(decisiones)[clave] = []
        return f"⏳ No he podido programarlo ahora ({str(e)[:120]}). Lo reintento yo solo en unos minutos."
    lineas = res["lineas"]
    if any(l.startswith(("✅", "🗓", "📲")) for l in lineas):
        decisiones.marcar_clip(clave, publicado=True)
    if res["fallidas"]:
        _pendientes(decisiones)[clave] = res["fallidas"]
        lineas.append("⏳ Lo que ha fallado lo reintento yo solo; no tienes que hacer nada.")
    return "\n".join(lineas)


def reintentar(estado: Estado, decisiones: Decisiones, maximo: int = 4) -> None:
    """Vuelve a intentar programar los clips aprobados que no se pudieron programar
    (por el límite diario, la cola llena de Buffer o un fallo puntual)."""
    if not publicar.buffer_configurado():
        return
    config = cargar_config().get("publicacion", {})
    pendientes = _pendientes(decisiones)
    # clips indicados a mano en la configuración, una sola vez:
    #   reintentar_youtube → solo YouTube;  añadir_instagram → solo Instagram
    hechos = decisiones.datos.setdefault("reintentados", [])
    for red, lista in (("youtube", config.get("reintentar_youtube")), ("instagram", config.get("añadir_instagram"))):
        for clave in lista or []:
            marca = clave if red == "youtube" else f"instagram:{clave}"
            if marca not in hechos:
                if clave not in pendientes:
                    pendientes[clave] = [red]
                elif pendientes[clave] and red not in pendientes[clave]:
                    pendientes[clave].append(red)
                hechos.append(marca)
    if not pendientes:
        return
    try:
        canales = publicar.canales_buffer()
    except Exception as e:
        log(f"No pude hablar con Buffer para los reintentos: {e}")
        return
    registro = _registro(decisiones, config)
    registro_otras = decisiones.datos.setdefault("otras_programadas", [])
    for clave in list(pendientes)[:maximo]:
        redes = pendientes[clave]
        info = estado.datos["clips"].get(clave, {}).get("publicar")
        if not info or not info.get("video_url"):
            pendientes.pop(clave)
            continue
        try:
            res = publicar.publicar_en_buffer(info, config, registro, solo=redes or None, canales=canales,
                                              registro_otras=registro_otras)
        except Exception as e:
            log(f"Reintento {clave} falló: {e}")
            continue
        log(f"Reintento {clave}: {' | '.join(res['lineas'])}")
        if res["fallidas"] and set(res["fallidas"]) == set(redes or res["fallidas"]):
            continue  # sigue igual: se reintentará más tarde sin molestarte
        bien = [l for l in res["lineas"] if not l.startswith("❌")]
        if bien:
            decisiones.marcar_clip(clave, publicado=True)
            telegram.mensaje(f"🔁 <b>Clip programado</b>\n{esc(info.get('titulo', ''))}\n{esc(chr(10).join(bien))}")
        if res["fallidas"]:
            pendientes[clave] = res["fallidas"]
        else:
            pendientes.pop(clave)
    decisiones.guardar()


def main() -> int:
    estado = Estado()          # solo lectura: datos de los clips enviados
    decisiones = Decisiones()  # aquí se guardan tus decisiones
    n = telegram.recoger_decisiones(decisiones, al_aprobar=lambda clave: al_aprobar(clave, estado, decisiones),
                                    al_panel=panel.texto_telegram)
    log(f"Decisiones procesadas: {n}")
    reintentar(estado, decisiones)
    try:
        panel.escribir_readme()
    except Exception as e:
        log(f"No pude actualizar el panel: {e}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        traceback.print_exc()
        telegram.mensaje(f"❌ <b>Error al publicar</b>\n<code>{esc(str(error)[:1500])}</code>")
        sys.exit(1)
