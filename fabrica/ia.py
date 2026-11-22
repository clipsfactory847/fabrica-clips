"""El 'editor jefe': Gemini lee lo que se dijo en cada momento candidato
y decide qué clips merecen la pena, dónde empiezan, dónde acaban y cómo se titulan."""
import json
import time

import requests

from .util import formato_tiempo, log, secreto

# Se prueban en orden; si Google retira uno, se usa el siguiente.
MODELOS = ["gemini-flash-latest", "gemini-3-flash-preview", "gemini-2.5-flash", "gemini-flash-lite-latest"]
URL = "https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"

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
    for modelo in MODELOS:
        for intento in range(4):
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
                log(f"Gemini no responde ({type(e).__name__}). Espero 30 s y lo reintento...")
                time.sleep(30)
                continue
            if r.status_code == 200:
                try:
                    texto = r.json()["candidates"][0]["content"]["parts"][0]["text"]
                    log(f"Gemini ({modelo}) ha respondido.")
                    return json.loads(texto)
                except (KeyError, IndexError, ValueError) as e:
                    ultimo_error = f"respuesta rara de {modelo}: {e}"
                    break
            if r.status_code in (429, 500, 503):
                log(f"Gemini ocupado o límite gratuito alcanzado ({r.status_code}). Espero 40 s...")
                time.sleep(40)
                continue
            ultimo_error = f"{modelo} → {r.status_code}: {r.text[:300]}"
            break  # probamos el siguiente modelo
    raise RuntimeError(f"No se pudo usar Gemini. Último error: {ultimo_error}")


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
- "motivo": en pocas palabras, por qué engancha (o por qué no).

Responde SOLO con JSON: {{"clips": [{{"id": "", "puntuacion": 0, "inicio": 0, "fin": 0, "titulo_pantalla": "", "titulo": "", "descripcion": "", "hashtags": [], "motivo": ""}}]}}

CLIPS:
{lista}
"""


def evaluar_clips_twitch(candidatos: list, cuenta: dict) -> list:
    """candidatos: [{'id','streamer','categoria','titulo','vistas','duracion','frases':[...]}]"""
    partes = []
    for c in candidatos:
        partes.append(f"\n### id={c['id']} | streamer: {c['streamer']} | categoría: {c['categoria']} | "
                      f"título: \"{c['titulo']}\" | {c['vistas']} visualizaciones | dura {c['duracion']:.0f} s")
        if c["frases"]:
            partes.extend(f"[{s['t0']:.0f}] {s['texto']}" for s in c["frases"])
        else:
            partes.append("(no se entiende lo que se dice o no hay voz)")
    respuesta = _llamar_gemini(CRITERIO_CLIPS.format(lista="\n".join(partes)))
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
        if not c or nota < cuenta.get("puntuacion_minima", 8):
            continue
        if fin - ini < dmin:  # si el recorte es demasiado corto, usamos el clip entero
            ini, fin = 0.0, c["duracion"]
        fin = min(fin, ini + dmax)
        if fin - ini < dmin:
            continue
        p.update(id=c["id"], puntuacion=nota, inicio=ini, fin=fin)
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
    respuesta = _llamar_gemini(prompt)
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
