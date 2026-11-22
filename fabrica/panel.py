"""Panel de control: resume el estado de la fábrica en la portada del proyecto (README.md)
y en un mensaje de Telegram cuando escribes /panel a tu bot."""
import os
import time
from datetime import datetime, timedelta

import requests

from .estado import Decisiones, Estado
from .util import RAIZ, cargar_config, log

ESTADO_RONDA = {"success": "✅", "failure": "❌", "cancelled": "⏹", None: "⏳"}


def _ultimas_rondas(n: int = 6) -> list:
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("GITHUB_TOKEN", "")
    if not repo:
        return []
    try:
        r = requests.get(f"https://api.github.com/repos/{repo}/actions/workflows/fabrica.yml/runs",
                         params={"per_page": n},
                         headers={"Authorization": f"Bearer {token}"} if token else {}, timeout=30)
        return r.json().get("workflow_runs", []) if r.status_code == 200 else []
    except Exception:
        return []


def _fecha(txt: str):
    try:
        return datetime.strptime(txt, "%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return None


def datos_panel() -> dict:
    est, dec = Estado().datos["clips"], Decisiones().datos["clips"]
    ahora = datetime.utcnow()
    hoy = ahora - timedelta(hours=24)
    semana = ahora - timedelta(days=7)

    def contar(desde):
        c = {"revisados": 0, "enviados": 0, "aprobados": 0, "descartados": 0, "publicados": 0}
        for k, v in est.items():
            f = _fecha(v.get("fecha"))
            if not f or f < desde:
                continue
            c["revisados"] += 1
            if v.get("resultado") == "enviado":
                c["enviados"] += 1
        for k, v in dec.items():
            f = _fecha(v.get("fecha"))
            if not f or f < desde:
                continue
            if v.get("decision") == "aprobado":
                c["aprobados"] += 1
            elif v.get("decision") == "descartado":
                c["descartados"] += 1
            if v.get("publicado"):
                c["publicados"] += 1
        return c

    enviados = sorted(((k, v) for k, v in est.items() if v.get("resultado") == "enviado"),
                      key=lambda kv: kv[1].get("fecha", ""), reverse=True)[:15]
    ultimos = []
    for k, v in enviados:
        d = dec.get(k, {})
        estado = "🚀 publicado" if d.get("publicado") else {
            "aprobado": "✅ aprobado", "descartado": "❌ descartado"}.get(d.get("decision"), "⏳ pendiente")
        ultimos.append({"fecha": v.get("fecha", ""), "streamer": v.get("streamer", ""),
                        "titulo": (v.get("publicar") or {}).get("titulo") or v.get("titulo", ""),
                        "nota": v.get("nota"), "vistas": v.get("vistas"), "estado": estado})
    return {"hoy": contar(hoy), "semana": contar(semana), "ultimos": ultimos,
            "pendientes": sum(1 for u in ultimos if u["estado"] == "⏳ pendiente"),
            "rondas": _ultimas_rondas()}


def _md(t) -> str:
    return str(t or "").replace("|", "/").replace("\n", " ")[:70]


def escribir_readme() -> None:
    d = datos_panel()
    cfg = cargar_config()
    cuenta = next((c for c in cfg.get("cuentas", []) if c.get("activa", True)), {})
    h, s = d["hoy"], d["semana"]
    lineas = [
        "# 🎬 Clipéalo · Panel de control",
        "",
        f"_Actualizado: {time.strftime('%d/%m/%Y %H:%M')} UTC · se actualiza solo cada pocos minutos_",
        "",
        "## Resumen",
        "",
        "| | Últimas 24 h | Últimos 7 días |",
        "|---|---|---|",
        f"| 🔎 Clips revisados por la IA | {h['revisados']} | {s['revisados']} |",
        f"| 📩 Enviados a tu Telegram | {h['enviados']} | {s['enviados']} |",
        f"| ✅ Aprobados | {h['aprobados']} | {s['aprobados']} |",
        f"| ❌ Descartados | {h['descartados']} | {s['descartados']} |",
        f"| 🚀 Enviados a publicar | {h['publicados']} | {s['publicados']} |",
        "",
        f"**Pendientes de revisar en Telegram:** {d['pendientes']}",
        "",
        "## Últimas rondas automáticas",
        "",
    ]
    if d["rondas"]:
        lineas += ["| Estado | Cuándo (UTC) | Detalle |", "|---|---|---|"]
        for r in d["rondas"]:
            icono = ESTADO_RONDA.get(r.get("conclusion"), "⏳")
            cuando = (r.get("created_at") or "")[:16].replace("T", " ")
            lineas.append(f"| {icono} | {cuando} | [ver]({r.get('html_url')}) |")
    else:
        lineas.append("_Sin datos todavía._")
    lineas += ["", "## Últimos clips enviados", ""]
    if d["ultimos"]:
        lineas += ["| Fecha (UTC) | Streamer | Título | Nota | Vistas Twitch | Estado |",
                   "|---|---|---|---|---|---|"]
        for u in d["ultimos"]:
            nota = f"{u['nota']:.0f}/10" if isinstance(u["nota"], (int, float)) else "-"
            lineas.append(f"| {u['fecha']} | {_md(u['streamer'])} | {_md(u['titulo'])} | {nota} | "
                          f"{u['vistas'] or '-'} | {u['estado']} |")
    else:
        lineas.append("_Todavía no se ha enviado ningún clip._")
    lineas += [
        "", "## Configuración actual", "",
        f"- **Categorías:** {', '.join(cuenta.get('categorias', []))}",
        f"- **Streamers vigilados:** {', '.join(cuenta.get('streamers', []))}",
        f"- **Nota mínima:** {cuenta.get('puntuacion_minima')} · **Clips por ronda:** {cuenta.get('clips_por_ejecucion')}"
        f" · **Rondas:** cada 4 horas",
        "- **Publicación:** Buffer → YouTube (automático) y TikTok (aviso al móvil)",
        "", "_Para cambiar la configuración, edita `config/cuentas.yaml`._", "",
    ]
    (RAIZ / "README.md").write_text("\n".join(lineas), encoding="utf-8")
    log("Panel actualizado en README.md")


def texto_telegram() -> str:
    d = datos_panel()
    h, s = d["hoy"], d["semana"]
    ronda = d["rondas"][0] if d["rondas"] else None
    r_txt = ""
    if ronda:
        r_txt = (f"\n\n<b>Última ronda:</b> {ESTADO_RONDA.get(ronda.get('conclusion'), '⏳')} "
                 f"{(ronda.get('created_at') or '')[11:16]} UTC")
    return (
        "📊 <b>Panel de Clipéalo</b>\n\n"
        f"<b>Últimas 24 h</b>\n🔎 Revisados: {h['revisados']}\n📩 Enviados: {h['enviados']}\n"
        f"✅ Aprobados: {h['aprobados']} · ❌ Descartados: {h['descartados']}\n🚀 A publicar: {h['publicados']}\n\n"
        f"<b>7 días</b>: {s['enviados']} enviados · {s['aprobados']} aprobados · {s['publicados']} publicados\n"
        f"⏳ Pendientes de revisar: {d['pendientes']}"
        f"{r_txt}\n\nPanel completo: github.com/{os.environ.get('GITHUB_REPOSITORY', '')}"
    )
