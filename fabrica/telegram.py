"""Envía mensajes y vídeos a tu Telegram y recoge tus respuestas (Aprobar / Descartar)."""
import html
import json
from pathlib import Path

import requests

from .util import log, secreto


def _api(metodo: str) -> str:
    return f"https://api.telegram.org/bot{secreto('TELEGRAM_TOKEN')}/{metodo}"


def esc(texto) -> str:
    return html.escape(str(texto))


def mensaje(texto: str, botones: list = None) -> None:
    datos = {
        "chat_id": secreto("TELEGRAM_CHAT_ID"), "text": texto[:4000],
        "parse_mode": "HTML", "disable_web_page_preview": "true",
    }
    if botones:
        datos["reply_markup"] = json.dumps({"inline_keyboard": botones})
    try:
        r = requests.post(_api("sendMessage"), data=datos, timeout=60)
        if r.status_code != 200:
            log(f"Telegram no aceptó el mensaje: {r.text[:300]}")
    except Exception as e:  # un fallo de aviso nunca debe parar el sistema
        log(f"No se pudo avisar por Telegram: {e}")


def video(ruta: Path, pie: str) -> None:
    with open(ruta, "rb") as f:
        r = requests.post(_api("sendVideo"), data={
            "chat_id": secreto("TELEGRAM_CHAT_ID"), "caption": pie[:1000],
            "parse_mode": "HTML", "supports_streaming": "true",
            "width": 1080, "height": 1920,
        }, files={"video": (ruta.name, f, "video/mp4")}, timeout=600)
    if r.status_code != 200:
        raise RuntimeError(f"Telegram no aceptó el vídeo: {r.text[:300]}")


def botones_decision(clave: str) -> list:
    return [[
        {"text": "✅ Aprobar", "callback_data": f"ok:{clave}"[:64]},
        {"text": "❌ Descartar", "callback_data": f"no:{clave}"[:64]},
    ]]


def recoger_decisiones(estado, al_aprobar=None, al_panel=None) -> int:
    """Lee los botones que has pulsado desde la última vez, los apunta y,
    si se indica, publica los aprobados (al_aprobar devuelve el texto del resultado)."""
    try:
        r = requests.get(_api("getUpdates"), params={
            "offset": estado.datos.get("telegram_offset", 0) + 1 if estado.datos.get("telegram_offset") else None,
            "timeout": 0, "allowed_updates": json.dumps(["callback_query", "message"]),
        }, timeout=60)
        updates = r.json().get("result", []) if r.status_code == 200 else []
    except Exception as e:
        log(f"No pude leer tus respuestas de Telegram: {e}")
        return 0

    contador = 0
    for u in updates:
        estado.datos["telegram_offset"] = u["update_id"]
        m = u.get("message")
        if m and al_panel and str(m.get("from", {}).get("id")) == secreto("TELEGRAM_CHAT_ID"):
            texto_m = (m.get("text") or "").strip().lower()
            if texto_m.startswith(("/panel", "/estado", "panel")):
                try:
                    mensaje(al_panel())
                except Exception as e:
                    mensaje(f"No pude preparar el panel: {esc(str(e)[:200])}")
            elif texto_m.startswith(("/start", "/ayuda", "/help")):
                mensaje("👋 Soy el bot de Clipéalo.\n\nTe mando los clips para aprobar con ✅ o descartar con ❌.\n"
                        "Escribe /panel para ver el resumen. (Respondo en unos minutos.)")
            continue
        q = u.get("callback_query")
        if not q or ":" not in q.get("data", ""):
            continue
        accion, clave = q["data"].split(":", 1)
        if accion not in ("ok", "no"):
            continue
        if str(q.get("from", {}).get("id")) != secreto("TELEGRAM_CHAT_ID"):
            continue  # solo cuentan tus pulsaciones
        aprobado = accion == "ok"
        estado.marcar_clip(clave, decision="aprobado" if aprobado else "descartado")
        contador += 1
        texto = "✅ Aprobado" if aprobado else "❌ Descartado. Apuntado para mejorar."
        if aprobado and al_aprobar:
            try:
                resultado = al_aprobar(clave)
            except Exception as e:
                resultado = f"❌ No se pudo publicar: {str(e)[:300]}"
            mensaje(f"<b>Clip aprobado</b>\n{esc(resultado)}")
            if any(x in resultado for x in ("✅", "🗓", "📲")):
                texto = "✅ Aprobado y enviado a publicar"
        try:
            requests.post(_api("answerCallbackQuery"), data={"callback_query_id": q["id"], "text": texto[:190]}, timeout=30)
            if q.get("message"):
                requests.post(_api("editMessageReplyMarkup"), data={
                    "chat_id": q["message"]["chat"]["id"], "message_id": q["message"]["message_id"],
                    "reply_markup": json.dumps({"inline_keyboard": [[{"text": texto[:60], "callback_data": "hecho:0"}]]}),
                }, timeout=30)
        except Exception:
            pass
    estado.guardar()
    if contador:
        log(f"Apuntadas {contador} decisiones tuyas de Telegram.")
    return contador
