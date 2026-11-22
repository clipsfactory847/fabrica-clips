"""Se ejecuta cada pocos minutos: lee los botones que has pulsado en Telegram
y publica los clips aprobados."""
import sys
import traceback

from . import publicar, telegram
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
    lineas = publicar.publicar_en_buffer(info, config)
    if any(l.startswith(("✅", "🗓", "📲")) for l in lineas):
        decisiones.marcar_clip(clave, publicado=True)
    return "\n".join(lineas)


def main() -> int:
    estado = Estado()          # solo lectura: datos de los clips enviados
    decisiones = Decisiones()  # aquí se guardan tus decisiones
    n = telegram.recoger_decisiones(decisiones, al_aprobar=lambda clave: al_aprobar(clave, estado, decisiones))
    log(f"Decisiones procesadas: {n}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        traceback.print_exc()
        telegram.mensaje(f"❌ <b>Error al publicar</b>\n<code>{esc(str(error)[:1500])}</code>")
        sys.exit(1)
