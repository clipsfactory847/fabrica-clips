"""Detecta automáticamente dónde está la cámara del streamer para elegir el formato del vídeo:

- "dividida":  cámara pequeña en una esquina (típico en partidas) → cara arriba, juego abajo.
- "camara":    la cámara ocupa casi todo (Just Chatting, IRL) → recorte vertical centrado en la cara.
- "clasica":   no se encuentra cara → imagen centrada con fondo desenfocado.
"""
from pathlib import Path

from .util import log

try:
    import cv2
    import numpy as np
except ImportError:  # si falta la librería, usamos siempre el formato clásico
    cv2 = None


def _caras_en(frame, detector) -> list:
    gris = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    h = gris.shape[0]
    caras = detector.detectMultiScale(gris, scaleFactor=1.1, minNeighbors=6,
                                      minSize=(int(h * 0.05), int(h * 0.05)))
    return [tuple(int(v) for v in c) for c in caras]


def detectar(video: Path, desfase: float, duracion: float, muestras: int = 10) -> dict:
    """Devuelve {'tipo': ..., 'W','H', y la caja de la cara 'cara': (x,y,w,h)}."""
    if cv2 is None:
        return {"tipo": "clasica"}
    cap = cv2.VideoCapture(str(video))
    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if not W or not H:
        return {"tipo": "clasica"}
    detector = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")

    detecciones = []
    for i in range(muestras):
        t = desfase + duracion * (i + 0.5) / muestras
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, frame = cap.read()
        if not ok:
            continue
        detecciones.extend(_caras_en(frame, detector))
    cap.release()
    if not detecciones:
        log("Formato: clásico (no se ve la cara del streamer)")
        return {"tipo": "clasica", "W": W, "H": H}

    # Nos quedamos con la cara que aparece siempre en el mismo sitio (la del streamer,
    # no caras sueltas del juego).
    mejor, votos = None, 0
    for c in detecciones:
        cx, cy = c[0] + c[2] / 2, c[1] + c[3] / 2
        grupo = [d for d in detecciones
                 if abs(d[0] + d[2] / 2 - cx) < W * 0.06 and abs(d[1] + d[3] / 2 - cy) < H * 0.08]
        if len(grupo) > votos:
            votos, mejor = len(grupo), grupo
    if votos < max(3, muestras // 3):
        log("Formato: clásico (no hay una cara fija)")
        return {"tipo": "clasica", "W": W, "H": H}

    caja = tuple(int(np.median([g[k] for g in mejor])) for k in range(4))
    x, y, w, h = caja
    if h / H >= 0.20:
        log("Formato: cámara a pantalla completa (recorte vertical)")
        return {"tipo": "camara", "W": W, "H": H, "cara": caja}
    log("Formato: pantalla dividida (cámara arriba, juego abajo)")
    return {"tipo": "dividida", "W": W, "H": H, "cara": caja}


def _par(v: float) -> int:
    return max(2, int(v) // 2 * 2)


def filtro_video(d: dict, ancho: int, alto: int, zoom: float, subs: str) -> tuple:
    """Devuelve (filtro de ffmpeg, posición Y de los subtítulos, posición Y del título)."""
    W, H = d.get("W"), d.get("H")

    if d["tipo"] == "dividida":
        alto_cam = 640
        alto_juego = alto - alto_cam
        x, y, w, h = d["cara"]
        # Recorte de la cámara: un rectángulo alrededor de la cara con la proporción del panel
        prop = ancho / alto_cam
        cw = min(W, w * 3.4)
        ch = cw / prop
        if ch > H:
            ch, cw = H, H * prop
        cx = min(max(x + w / 2 - cw / 2, 0), W - cw)
        cy = min(max(y + h / 2 - ch * 0.45, 0), H - ch)
        # Recorte del juego: zona central a toda altura
        gw = min(W, H * ancho / alto_juego)
        gx = (W - gw) / 2
        filtro = (
            f"[0:v]split=2[a][b];"
            f"[a]crop={_par(cw)}:{_par(ch)}:{_par(cx)}:{_par(cy)},scale={ancho}:{alto_cam},setsar=1[cam];"
            f"[b]crop={_par(gw)}:{_par(H)}:{_par(gx)}:0,scale={ancho}:{alto_juego},setsar=1[juego];"
            f"[cam][juego]vstack=inputs=2,ass={subs},fps=30[v]"
        )
        return filtro, 1480, alto_cam

    if d["tipo"] == "camara":
        x, y, w, h = d["cara"]
        cw = min(W, H * ancho / alto)
        cx = min(max(x + w / 2 - cw / 2, 0), W - cw)
        filtro = (
            f"[0:v]crop={_par(cw)}:{_par(H)}:{_par(cx)}:0,scale={ancho}:{alto},setsar=1,"
            f"ass={subs},fps=30[v]"
        )
        return filtro, 1420, 260

    ancho_central = int(ancho * zoom) // 2 * 2
    filtro = (
        f"[0:v]split=2[a][b];"
        f"[a]scale={ancho}:{alto}:force_original_aspect_ratio=increase,crop={ancho}:{alto},"
        f"scale=270:480,boxblur=10:2,scale={ancho}:{alto},eq=brightness=-0.12[fondo];"
        f"[b]scale={ancho_central}:-2,crop={ancho}:ih[centro];"
        f"[fondo][centro]overlay=(W-w)/2:(H-h)/2,ass={subs},fps=30[v]"
    )
    return filtro, 1430, 260
