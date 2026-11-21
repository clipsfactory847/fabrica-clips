"""Convierte un trozo horizontal del directo en un Short vertical editado:
fondo desenfocado, imagen central ampliada, título arriba y subtítulos grandes."""
from pathlib import Path

from .util import duracion_video, ejecutar, log

ANCHO, ALTO = 1080, 1920
COLORES = {  # formato de color de los subtítulos (&HAABBGGRR)
    "amarillo": "&H0000FFFF",
    "blanco": "&H00FFFFFF",
    "verde": "&H0000FF7F",
}
LIMITE_TELEGRAM = 48 * 1024 * 1024


def _tiempo_ass(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600)
    m = int(t % 3600 // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _limpiar(texto: str) -> str:
    return texto.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def _grupos_de_palabras(palabras: list, por_grupo: int) -> list:
    grupos, actual = [], []
    for w in palabras:
        if actual and (len(actual) >= por_grupo or w["t0"] - actual[-1]["t1"] > 0.6):
            grupos.append(actual)
            actual = []
        actual.append(w)
    if actual:
        grupos.append(actual)
    return grupos


def crear_subtitulos(palabras: list, titulo: str, duracion: float, estilo: dict, destino: Path) -> Path:
    color = COLORES.get(estilo.get("color_subtitulos", "amarillo"), COLORES["amarillo"])
    lineas = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {ANCHO}", f"PlayResY: {ALTO}",
        "WrapStyle: 0", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Sub,DejaVu Sans,82,{color},&H00FFFFFF,&H00000000,&H96000000,-1,0,0,0,100,100,0,0,1,7,3,5,60,60,0,1",
        "Style: Titulo,DejaVu Sans,62,&H00141414,&H00FFFFFF,&H00FFFFFF,&H00000000,-1,0,0,0,100,100,0,0,3,22,0,8,90,90,250,1",
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    if titulo and estilo.get("titulo_en_pantalla", True):
        lineas.append(f"Dialogue: 1,{_tiempo_ass(0)},{_tiempo_ass(duracion)},Titulo,,0,0,0,,{_limpiar(titulo)}")

    grupos = _grupos_de_palabras(palabras, int(estilo.get("palabras_por_subtitulo", 3)))
    for i, g in enumerate(grupos):
        inicio = g[0]["t0"]
        fin = g[-1]["t1"] + 0.25
        if i + 1 < len(grupos):
            fin = min(fin, grupos[i + 1][0]["t0"])
        fin = min(max(fin, inicio + 0.3), duracion)
        if inicio >= duracion:
            break
        texto = _limpiar(" ".join(w["w"] for w in g)).upper()
        # pequeño efecto de "salto" al aparecer cada subtítulo
        efecto = r"{\pos(540,1430)\fscx85\fscy85\t(0,90,\fscx100\fscy100)}"
        lineas.append(f"Dialogue: 0,{_tiempo_ass(inicio)},{_tiempo_ass(fin)},Sub,,0,0,0,,{efecto}{texto}")

    destino.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    return destino


def editar_clip(entrada: Path, salida: Path, desfase: float, duracion: float,
                palabras_relativas: list, titulo: str, estilo: dict) -> Path:
    """desfase = segundo dentro del archivo descargado donde empieza el clip."""
    carpeta = salida.parent
    real = duracion_video(entrada)
    desfase = max(0.0, min(desfase, max(0.0, real - 1)))
    duracion = min(duracion, real - desfase)

    subs = crear_subtitulos(palabras_relativas, titulo, duracion, estilo, carpeta / f"{salida.stem}.ass")
    zoom = float(estilo.get("zoom", 1.25))
    ancho_central = int(ANCHO * zoom) // 2 * 2

    filtro = (
        f"[0:v]split=2[a][b];"
        f"[a]scale={ANCHO}:{ALTO}:force_original_aspect_ratio=increase,crop={ANCHO}:{ALTO},"
        f"scale=270:480,boxblur=10:2,scale={ANCHO}:{ALTO},eq=brightness=-0.12[fondo];"
        f"[b]scale={ancho_central}:-2,crop={ANCHO}:ih[centro];"
        f"[fondo][centro]overlay=(W-w)/2:(H-h)/2,ass={subs.name},fps=30[v]"
    )

    def codificar(calidad: int):
        ejecutar([
            "ffmpeg", "-y", "-v", "error",
            "-ss", f"{desfase:.3f}", "-i", str(entrada), "-t", f"{duracion:.3f}",
            "-filter_complex", filtro, "-map", "[v]", "-map", "0:a?",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", str(calidad), "-pix_fmt", "yuv420p",
            "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
            "-movflags", "+faststart", salida.name,
        ], cwd=carpeta)

    codificar(21)
    if salida.stat().st_size > LIMITE_TELEGRAM:
        log("El vídeo pesa demasiado para Telegram, lo comprimo un poco más...")
        codificar(27)
    log(f"Clip editado: {salida.name} ({salida.stat().st_size // 1_000_000} MB, {duracion:.0f} s)")
    return salida
