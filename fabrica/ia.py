"""El 'editor jefe': Gemini lee lo que se dijo en cada momento candidato
y decide qué clips merecen la pena, dónde empiezan, dónde acaban y cómo se titulan."""
import json
import time

import requests

from .util import formato_tiempo, log, secreto

# Preferidos, en orden. Además, el bot pregunta a Google qué modelos "flash" hay disponibles
# hoy, así que si Google retira uno o saca uno nuevo, se adapta solo.
PREFERIDOS = ["gemini-flash-latest", "gemini-3.8-flash", "gemini-3-flash-preview", "gemini-flash-lite-latest"]
URL = "https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"
URL_LISTA = "https://generativelanguage.googleapis.com/v1beta/models"
_RETIRADOS = set()  # modelos que Google ha dicho que ya no existen (en esta ronda)


class GeminiSaturado(RuntimeError):
    """Gemini no ha respondido (saturado o límite gratuito). Se reintenta en la siguiente ronda."""


def _modelos_disponibles(clave: str) -> list:
    """Preferidos primero; luego cualquier otro modelo 'flash' de texto que Google tenga activo."""
    lista = []
    try:
        r = requests.get(URL_LISTA, params={"key": clave, "pageSize": 200}, timeout=30)
        if r.status_code == 200:
            for m in r.json().get("models", []):
                nombre = m.get("name", "").split("/")[-1]
                if ("generateContent" in m.get("supportedGenerationMethods", []) and "flash" in nombre
                        and not any(x in nombre for x in ("tts", "image", "audio", "live", "embedding", "exp"))):
                    lista.append(nombre)
    except requests.RequestException:
        pass
    activos = set(lista)
    orden = [m for m in PREFERIDOS if not activos or m in activos]
    # el resto, de más nuevo a más viejo
    extra = sorted((m for m in lista if m not in orden), reverse=True)
    return [m for m in orden + extra if m not in _RETIRADOS][:6]

INSTRUCCIONES = """Eres el editor jefe de un canal de YouTube Shorts en español de España que publica los mejores momentos de streamers.
Te paso la transcripción de varios fragmentos de un directo de {streamer} (título del directo: "{titulo}").
Cada línea empieza por el segundo exacto del directo en el que se dijo.

Tu trabajo: elegir los momentos con MÁS potencial viral y entretenido para un Short.
Buscas: momentos graciosos, reacciones exageradas, situaciones inesperadas o absurdas, frases memorables,
tensión, jugadas espectaculares, conversaciones muy interesantes o polémicas sanas.
Descarta: momentos aburridos, conversaciones sin gracia, cosas que no se entienden sin más contexto,
lectura de donaciones normales, publicidad, silencios.

Reglas para cada clip:
- Debe estar ENTERO dentro de UN solo fragmento (respeta sus límites de inicio y fin).
- Duración entre {dmin} y {dmax} segundos.
- Empieza un poco ANTES de lo que lo provoca, para que se entienda el contexto. Termina justo después de la reacción o el remate. Nada de relleno.
- "inicio" y "fin" son segundos del directo (números enteros, sacados de las marcas de la transcripción).
- "puntuacion": de 1 a 10, sé exigente (un 9-10 es algo que la gente compartiría seguro).
- "titulo_pantalla": frase corta y llamativa para poner encima del vídeo, máximo 7 palabras, sin hashtags ni emojis.
- "titulo": título para YouTube de máximo 80 caracteres, llamativo pero sin mentir, puede incluir 1 emoji.
- "descripcion": 1 o 2 frases que expliquen qué pasa y mencionen a {streamer}.
- "hashtags": entre 3 y 6 hashtags relevantes (empiezan por #).
- "motivo": en pocas palabras, por qué este momento funciona.

Elige como máximo {maximo} clips y no repitas el mismo momento. Si nada merece la pena, devuelve una lista vacía.
Responde SOLO con JSON con esta forma exacta:
{{"clips": [{{"inicio": 0, "fin": 0, "puntuacion": 0, "titulo_pantalla": "", "titulo": "", "descripcion": "", "hashtags": [], "motivo": ""}}]}}

FRAGMENTOS:
{fragmentos}
"""


def _texto_fragmentos(fragmentos: list) -> str:
    partes = []
    for i, f in enumerate(fragmentos, 1):
        partes.append(f"\n### Fragmento {i} (del segundo {int(f['inicio'])} al {int(f['fin'])}, "
                      f"minuto {formato_tiempo(f['inicio'])})")
        for s in f["frases"]:
            partes.append(f"[{int(s['t0'])}] {s['texto']}")
    return "\n".join(partes)


def _llamar_gemini(prompt: str) -> dict:
    clave = secreto("GEMINI_API_KEY")
    ultimo_error = ""
    saturado = False
    for vuelta in range(1):  # si todo está saturado, pasamos a la IA de reserva (Groq)
        if vuelta:
            log("Gemini está saturado. Espero 2 minutos y lo intento otra vez con todos los modelos...")
            time.sleep(120)
        for modelo in _modelos_disponibles(clave):
            for intento, espera in enumerate((15, 30)):
                try:
                    r = requests.post(
                        URL.format(modelo=modelo),
                        params={"key": clave},
                        json={
                            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                            "generationConfig": {"temperature": 0.4, "responseMimeType": "application/json"},
                        },
                        timeout=180,
                    )
                except requests.RequestException as e:
                    ultimo_error = f"{modelo} no respondió a tiempo ({type(e).__name__})"
                    saturado = True
                    log(f"Gemini ({modelo}) no responde. Espero {espera} s...")
                    time.sleep(espera)
                    continue
                if r.status_code == 200:
                    try:
                        texto = r.json()["candidates"][0]["content"]["parts"][0]["text"]
                        log(f"Gemini ({modelo}) ha respondido.")
                        return json.loads(texto)
                    except (KeyError, IndexError, ValueError) as e:
                        ultimo_error = f"respuesta rara de {modelo}: {e}"
                        break
                if r.status_code in (429, 500, 503, 504):
                    ultimo_error = f"{modelo} → {r.status_code} (saturado o límite gratuito)"
                    saturado = True
                    log(f"Gemini ({modelo}) ocupado ({r.status_code}). Espero {espera} s...")
                    time.sleep(espera)
                    continue
                if r.status_code == 404:
                    _RETIRADOS.add(modelo)
                    log(f"El modelo {modelo} ya no existe; paso al siguiente.")
                ultimo_error = f"{modelo} → {r.status_code}: {r.text[:200]}"
                break  # probamos el siguiente modelo
        if not saturado:
            break
    if saturado:
        raise GeminiSaturado(f"Gemini saturado. Último error: {ultimo_error}")
    raise RuntimeError(f"No se pudo usar Gemini. Último error: {ultimo_error}")


# ---------------------------------------------------------------------------
#  IA DE RESERVA: Groq (gratis, misma clave que los subtítulos)
# ---------------------------------------------------------------------------
GROQ_CHAT = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODELOS_URL = "https://api.groq.com/openai/v1/models"
GROQ_PREFERIDOS = ["openai/gpt-oss-120b", "qwen/qwen3.8-27b", "llama-3.3-70b-versatile", "openai/gpt-oss-20b"]


def _modelos_groq(clave: str) -> list:
    activos = []
    try:
        r = requests.get(GROQ_MODELOS_URL, headers={"Authorization": f"Bearer {clave}"}, timeout=30)
        if r.status_code == 200:
            activos = [m["id"] for m in r.json().get("data", []) if m.get("active", True)
                       and not any(x in m["id"] for x in ("whisper", "guard", "tts", "playai", "orpheus", "compound"))]
    except requests.RequestException:
        pass
    orden = [m for m in GROQ_PREFERIDOS if not activos or m in activos]
    return orden + [m for m in activos if m not in orden][:3]


def _llamar_groq(prompt: str) -> dict:
    clave = secreto("GROQ_API_KEY")
    ultimo = ""
    for modelo in _modelos_groq(clave):
        cuerpo = {"model": modelo, "temperature": 0.4, "max_completion_tokens": 3000,
                  "response_format": {"type": "json_object"},
                  "messages": [{"role": "user", "content": prompt}]}
        if modelo.startswith("openai/gpt-oss"):
            cuerpo["reasoning_effort"] = "low"
        for intento in range(5):
            try:
                r = requests.post(GROQ_CHAT, headers={"Authorization": f"Bearer {clave}"}, json=cuerpo, timeout=120)
            except requests.RequestException as e:
                ultimo = f"{modelo}: {type(e).__name__}"
                time.sleep(15)
                continue
            if r.status_code == 200:
                try:
                    texto = r.json()["choices"][0]["message"]["content"]
                    return json.loads(texto[texto.find("{"): texto.rfind("}") + 1])
                except (KeyError, IndexError, ValueError) as e:
                    ultimo = f"respuesta rara de {modelo}: {e}"
                    break
            if r.status_code in (429, 500, 502, 503):
                try:
                    espera = min(65, float(r.headers.get("retry-after", 20)) + 2)
                except ValueError:
                    espera = 20
                ultimo = f"{modelo} → {r.status_code}"
                log(f"Groq ({modelo}) ocupado o límite por minuto ({r.status_code}). Espero {espera:.0f} s...")
                time.sleep(espera)
                continue
            ultimo = f"{modelo} → {r.status_code}: {r.text[:200]}"
            break
    raise GeminiSaturado(f"Tampoco responde la IA de reserva (Groq). Último error: {ultimo}")


def _evaluar_con_groq(bloques: list, tendencias: str, por_tanda: int = 4) -> dict:
    """Groq gratis admite textos más cortos: valoramos los clips en tandas pequeñas."""
    tend = "\n".join((tendencias or "(sin datos)").splitlines()[:12])
    clips = []
    for i in range(0, len(bloques), por_tanda):
        tanda = bloques[i:i + por_tanda]
        prompt = CRITERIO_CLIPS.format(lista="\n".join(tanda), tendencias=tend)
        res = _llamar_groq(prompt)
        clips.extend(res.get("clips", []) if isinstance(res, dict) else [])
    log(f"La IA de reserva (Groq) ha valorado {len(bloques)} clips.")
    return {"clips": clips}


CRITERIO_CLIPS = """Eres el editor jefe de un canal de YouTube Shorts en español de España que quiere MUCHAS visualizaciones.
Te paso clips de Twitch que los espectadores han creado y que se están viendo mucho. Para cada uno tienes:
streamer, categoría, título que le puso el espectador, visualizaciones en Twitch, duración y lo que se dice (con el segundo exacto).

Puntúa cada clip del 1 al 10 según su potencial en Shorts para una audiencia GENERAL (no solo fans del streamer).
Un clip merece 8-10 solo si cumple casi todo esto:
- Engancha en los primeros 2-3 segundos (algo pasa YA: un grito, una situación absurda, una frase potente, una jugada).
- Se entiende SIN contexto previo y sin conocer al streamer.
- Provoca una emoción fuerte: risa, sorpresa, tensión, rabia, ternura o "no puede ser".
- Tiene un remate o final claro que hace que la gente lo vea entero o lo repita.
- Funciona para cualquiera: jugadas espectaculares, fails épicos, situaciones locas en GTA/IRL, reacciones exageradas,
  bromas, sustos, momentos que se compartirían con amigos.
PRIORIDAD MÁXIMA: el HUMOR. Lo que mejor funciona en esta cuenta son momentos GRACIOSOS y absurdos:
situaciones locas en GTA (GTA VI, GTA V, roleplay), caídas y fails, bromas, frases inesperadas, reacciones
exageradas del streamer, sustos, cosas que salen fatal. Un clip gracioso y corto (15-35 s) vale más que uno
"interesante" pero serio. Si dudas entre dos, elige el que haga reír.
JUEGOS QUE MÁS SE VEN: Fortnite y GTA (GTA VI, GTA V y roleplay) son lo que más visitas da en esta cuenta.
A igualdad de calidad, puntúa 1 punto más alto un clip de Fortnite o GTA. Un clip aburrido sigue siendo aburrido
aunque sea de estos juegos.
ESTILOS: la mayoría de lo que publicamos es HUMOR, pero también queremos variedad. Indica en "estilo" cuál es:
"humor" (fails, bromas, situaciones absurdas, frases graciosas), "jugada" (jugada épica, clutch, victoria
increíble), "susto" (sustos, tensión, momentos de miedo), "reaccion" (reacción exagerada o emotiva) u "otro".
USA LAS TENDENCIAS: más abajo tienes los clips en español que se están haciendo MÁS virales en Twitch
en las últimas horas. Fíjate en qué streamers, juegos y tipos de momentos están petándolo ahora mismo y
sube la nota a los clips que encajen con esa ola (mismo streamer, mismo juego, mismo tipo de humor o situación),
porque son los que la gente está buscando hoy. Las "vistas/hora" indican lo rápido que se está viralizando un clip.
Puntúa BAJO (1-5): conversaciones de nicho, temas de esports o metajuego que solo entienden expertos, chistes internos,
lectura de donaciones, publicidad, momentos que necesitan haber visto el directo, audio sin gracia, clips donde no pasa nada.

Además, propón el recorte ideal DENTRO del clip ("inicio" y "fin" en segundos del clip): quita el principio muerto
para que empiece directamente en la acción y corta justo después del remate.

Para cada clip devuelve:
- "id": el id que te doy.
- "puntuacion", "inicio", "fin".
- "titulo_pantalla": gancho corto para poner encima del vídeo, máximo 7 palabras, sin hashtags ni emojis.
  Debe generar curiosidad (ej.: "No esperaba este final", "Le sale fatal la broma").
- "titulo": título de YouTube de máximo 80 caracteres, llamativo pero sin mentir, puede tener 1 emoji y debe nombrar al streamer.
- "descripcion": 1-2 frases que expliquen qué pasa, mencionando al streamer y que el clip es de su directo en Twitch.
- "hashtags": 3-6 hashtags relevantes.
- "estilo": "humor", "jugada", "susto", "reaccion" u "otro".
- "motivo": en pocas palabras, por qué engancha (o por qué no).

Responde SOLO con JSON: {{"clips": [{{"id": "", "puntuacion": 0, "inicio": 0, "fin": 0, "estilo": "", "titulo_pantalla": "", "titulo": "", "descripcion": "", "hashtags": [], "motivo": ""}}]}}

LO MÁS VIRAL EN TWITCH EN ESPAÑOL AHORA MISMO (para orientarte):
{tendencias}

CLIPS A PUNTUAR:
{lista}
"""


ULTIMO_MEJOR = None  # el mejor clip de la ronda aunque no llegue a la nota mínima


def evaluar_clips_twitch(candidatos: list, cuenta: dict, tendencias: str = "") -> list:
    global ULTIMO_MEJOR
    ULTIMO_MEJOR = None
    """candidatos: [{'id','streamer','categoria','titulo','vistas','duracion','frases':[...]}]"""
    bloques = []
    for c in candidatos:
        b = [f"\n### id={c['id']} | streamer: {c['streamer']} | categoría: {c['categoria']} | "
             f"título: \"{c['titulo']}\" | {c['vistas']} visualizaciones ({c.get('por_hora', '?')} vistas/hora) | dura {c['duracion']:.0f} s"]
        if c["frases"]:
            b.extend(f"[{s['t0']:.0f}] {s['texto']}" for s in c["frases"])
        else:
            b.append("(no se entiende lo que se dice o no hay voz)")
        bloques.append("\n".join(b))
    try:
        respuesta = _llamar_gemini(CRITERIO_CLIPS.format(lista="\n".join(bloques), tendencias=tendencias or "(sin datos)"))
    except GeminiSaturado as e:
        log(f"{e}. Paso a la IA de reserva (Groq)...")
        respuesta = _evaluar_con_groq(bloques, tendencias)
    propuestos = respuesta.get("clips", []) if isinstance(respuesta, dict) else []

    por_id = {c["id"]: c for c in candidatos}
    dmin, dmax = cuenta.get("duracion_min", 12), cuenta.get("duracion_max", 58)
    validos = []
    for p in propuestos:
        c = por_id.get(str(p.get("id")))
        try:
            nota = float(p.get("puntuacion", 0))
            ini = max(0.0, float(p.get("inicio", 0)))
            fin = min(float(p.get("fin", c["duracion"])), c["duracion"]) if c else 0
        except (TypeError, ValueError):
            continue
        if not c:
            continue
        if fin - ini < dmin:  # si el recorte es demasiado corto, usamos el clip entero
            ini, fin = 0.0, c["duracion"]
        fin = min(fin, ini + dmax)
        if fin - ini < dmin:
            continue
        p.update(id=c["id"], puntuacion=nota, inicio=ini, fin=fin)
        if ULTIMO_MEJOR is None or nota > ULTIMO_MEJOR["puntuacion"]:
            ULTIMO_MEJOR = p
        if nota >= cuenta.get("puntuacion_minima", 8):
            validos.append(p)
    validos.sort(key=lambda p: p["puntuacion"], reverse=True)
    log(f"La IA ha puntuado {len(propuestos)} clips; {len(validos)} superan la nota mínima.")
    return validos


def elegir_clips(fragmentos: list, streamer: str, titulo: str, cuenta: dict) -> list:
    dmin, dmax = cuenta.get("duracion_min", 15), cuenta.get("duracion_max", 58)
    maximo = cuenta.get("clips_por_directo", 4)
    prompt = INSTRUCCIONES.format(
        streamer=streamer, titulo=titulo, dmin=dmin, dmax=dmax,
        maximo=maximo + 2, fragmentos=_texto_fragmentos(fragmentos),
    )
    try:
        respuesta = _llamar_gemini(prompt)
    except GeminiSaturado as e:
        log(f"{e}. Paso a la IA de reserva (Groq)...")
        respuesta = _llamar_groq(prompt)
    propuestos = respuesta.get("clips", []) if isinstance(respuesta, dict) else []

    # Comprobamos que lo que propone la IA tiene sentido.
    validos = []
    for c in propuestos:
        try:
            ini, fin, nota = float(c["inicio"]), float(c["fin"]), float(c.get("puntuacion", 0))
        except (KeyError, TypeError, ValueError):
            continue
        frag = next((f for f in fragmentos if f["inicio"] - 2 <= ini <= f["fin"]), None)
        if not frag:
            continue
        ini = max(ini, frag["inicio"])
        fin = min(fin, frag["fin"])
        if fin - ini > dmax:
            fin = ini + dmax
        if fin - ini < dmin or nota < cuenta.get("puntuacion_minima", 7):
            continue
        if any(not (fin <= v["inicio"] or ini >= v["fin"]) for v in validos):
            continue
        c.update(inicio=ini, fin=fin, puntuacion=nota)
        validos.append(c)

    validos.sort(key=lambda c: c["puntuacion"], reverse=True)
    log(f"La IA propuso {len(propuestos)} clips; {len(validos[:maximo])} pasan el filtro de calidad.")
    return validos[:maximo]
