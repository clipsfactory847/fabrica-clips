"""Se ejecuta cada pocos minutos: lee los botones que has pulsado en Telegram
y publica los clips aprobados."""
import sys
import traceback

from . import panel, publicar, telegram
from .estado import Decisiones, Estado
from .telegram import esc
from .util import cargar_config, log


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
    registro = decisiones.datos.setdefault("youtube_programados", list(config.get("youtube_ya_subidos", [])))
    lineas = publicar.publicar_en_buffer(info, config, registro)
    if any(l.startswith(("✅", "🗓", "📲")) for l in lineas):
        decisiones.marcar_clip(clave, publicado=True)
    return "\n".join(lineas)


def reintentar_youtube(estado: Estado, decisiones: Decisiones) -> None:
    """Vuelve a programar en YouTube los clips que YouTube rechazó por su límite diario
    (se indican una vez en config/cuentas.yaml → publicacion.reintentar_youtube)."""
    config = cargar_config().get("publicacion", {})
    hechos = decisiones.datos.setdefault("reintentados", [])
    pendientes = [c for c in config.get("reintentar_youtube", []) if c not in hechos]
    if not pendientes or not publicar.buffer_configurado():
        return
    registro = decisiones.datos.setdefault("youtube_programados", list(config.get("youtube_ya_subidos", [])))
    for clave in pendientes:
        info = estado.datos["clips"].get(clave, {}).get("publicar")
        if not info or not info.get("video_url"):
            hechos.append(clave)
            continue
        lineas = publicar.publicar_en_buffer(info, config, registro, solo=["youtube"])
        log(f"Reintento {clave}: {' | '.join(lineas)}")
        if not any(l.startswith("❌") for l in lineas):
            hechos.append(clave)
            telegram.mensaje(f"🔁 <b>Vídeo reprogramado en YouTube</b>\n{esc(info.get('titulo', ''))}\n{esc(chr(10).join(lineas))}")
    decisiones.guardar()


def main() -> int:
    estado = Estado()          # solo lectura: datos de los clips enviados
    decisiones = Decisiones()  # aquí se guardan tus decisiones
    n = telegram.recoger_decisiones(decisiones, al_aprobar=lambda clave: al_aprobar(clave, estado, decisiones),
                                    al_panel=panel.texto_telegram)
    log(f"Decisiones procesadas: {n}")
    reintentar_youtube(estado, decisiones)
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
