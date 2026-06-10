from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import httpx, os, json
from datetime import datetime
from supabase import create_client

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
SUPABASE_URL  = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY  = os.environ.get("SUPABASE_KEY", "")

def get_db():
    return create_client(SUPABASE_URL, SUPABASE_KEY)

# ── Contexto base (Nivel 1) ────────────────────────────────────────────────────
BASE_CONTEXT = """
CONTEXTO FIJO DE ROEL JOYAS:
- Empresa de joyería mayorista con operaciones en Colombia, México y Chile.
- El dueño/gerente usa este agente para tomar decisiones de compra y venta de oro.
- Compras típicas: menos de $10,000 USD por operación.
- Unidad de trabajo: gramos (no onzas ni kilos).
- Monedas relevantes: USD (principal) y COP (Colombia).
- Decisiones clave: cuándo comprar, a qué precio comprar, cuándo vender o liquidar.
- El agente NO inventa precios — siempre busca el precio spot real en la web antes de responder.
- Precio por gramo = precio onza troy / 31.1035.
"""

SYSTEM_TEMPLATE = """Eres el agente de mercado de oro de Roel Joyas. Apoyas al dueño/gerente a tomar decisiones de compra y venta.

{base_context}

{memoria_dinamica}

INSTRUCCIONES DE RESPUESTA:
1. Busca en la web el precio spot actual (XAU/USD) y tipo de cambio USD/COP antes de responder sobre precios.
2. Reporta siempre en USD/gramo y COP/gramo.
3. Da señal clara: COMPRAR, ESPERAR o VENDER.
4. Si el usuario menciona que tomó una decisión importante (compró X gramos, vendió, esperó), extrae esa información y al FINAL de tu respuesta agrega una línea especial así:
   [DECISION: accion=COMPRA|VENTA|ESPERA, cantidad_gramos=X, precio_usd_gramo=Y, notas=texto breve]
   Solo si hay una decisión real. No la inventes.
5. Máximo 220 palabras. Español siempre.
6. Sé directo y práctico — hablas con el dueño del negocio, no con un analista."""

# ── Cargar memoria dinámica (Nivel 2) ─────────────────────────────────────────
def cargar_memoria(db) -> str:
    try:
        # Últimos aprendizajes
        aprendizajes = db.table("aprendizajes").select("*").order("created_at", desc=True).limit(10).execute()
        # Últimas decisiones
        decisiones = db.table("decisiones").select("*").order("created_at", desc=True).limit(5).execute()

        memoria = ""
        if aprendizajes.data:
            memoria += "\nAPRENDIZAJES PREVIOS DEL MERCADO Y DEL USUARIO:\n"
            for a in aprendizajes.data:
                fecha = a["created_at"][:10]
                memoria += f"- [{fecha}] {a['contenido']}\n"

        if decisiones.data:
            memoria += "\nDECISIONES RECIENTES DEL USUARIO:\n"
            for d in decisiones.data:
                fecha = d["created_at"][:10]
                accion = d.get("accion", "")
                gramos = d.get("cantidad_gramos", "")
                precio = d.get("precio_usd_gramo", "")
                notas = d.get("notas", "")
                memoria += f"- [{fecha}] {accion} {gramos}g a ${precio}/g — {notas}\n"

        return memoria if memoria else "\n(Sin historial previo aún — primera sesión.)\n"
    except:
        return "\n(Memoria no disponible en este momento.)\n"

# ── Guardar aprendizaje después de sesión ─────────────────────────────────────
async def generar_aprendizaje(messages: list, db):
    """Llama a Claude para que extraiga qué aprendió de la conversación."""
    try:
        resumen_prompt = f"""Analiza esta conversación sobre el mercado del oro y extrae máximo 3 aprendizajes concretos y útiles para futuras consultas. 

Pueden ser sobre: el estado del mercado en ese momento, patrones observados, preocupaciones del usuario, contexto relevante.

Conversación:
{json.dumps(messages[-6:], ensure_ascii=False)}

Responde SOLO con un JSON así, sin texto adicional:
{{"aprendizajes": ["aprendizaje 1", "aprendizaje 2", "aprendizaje 3"]}}"""

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": ANTHROPIC_KEY,
                    "anthropic-version": "2023-06-01",
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 300,
                    "messages": [{"role": "user", "content": resumen_prompt}]
                }
            )
        data = resp.json()
        text = ""
        for b in data.get("content", []):
            if b.get("type") == "text":
                text += b["text"]
        match = __import__("re").search(r'\{[\s\S]*\}', text)
        if match:
            parsed = json.loads(match.group())
            for ap in parsed.get("aprendizajes", []):
                if ap.strip():
                    db.table("aprendizajes").insert({"contenido": ap.strip()}).execute()
    except Exception as e:
        print(f"Error generando aprendizaje: {e}")

# ── Guardar decisión si el agente la detectó ──────────────────────────────────
def guardar_decision(respuesta: str, db):
    import re
    match = re.search(r'\[DECISION:(.*?)\]', respuesta)
    if not match:
        return
    try:
        raw = match.group(1).strip()
        partes = {}
        for item in raw.split(","):
            if "=" in item:
                k, v = item.split("=", 1)
                partes[k.strip()] = v.strip()
        db.table("decisiones").insert({
            "accion": partes.get("accion", ""),
            "cantidad_gramos": float(partes.get("cantidad_gramos", 0) or 0),
            "precio_usd_gramo": float(partes.get("precio_usd_gramo", 0) or 0),
            "notas": partes.get("notas", ""),
        }).execute()
    except Exception as e:
        print(f"Error guardando decisión: {e}")

# ── Guardar mensaje en historial ───────────────────────────────────────────────
def guardar_mensaje(role: str, content: str, session_id: str, db):
    try:
        db.table("mensajes").insert({
            "session_id": session_id,
            "role": role,
            "content": content[:2000],
        }).execute()
    except:
        pass

# ── Endpoints ──────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def index():
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()

@app.post("/api")
async def proxy(request: Request):
    body = await request.json()
    db = get_db()

    session_id = body.get("session_id", "default")
    messages   = body.get("messages", [])

    # Guardar mensaje del usuario
    if messages:
        last = messages[-1]
        if last.get("role") == "user":
            guardar_mensaje("user", last["content"], session_id, db)

    # Construir system prompt con memoria
    memoria = cargar_memoria(db)
    system  = SYSTEM_TEMPLATE.format(base_context=BASE_CONTEXT, memoria_dinamica=memoria)

    # Llamar a Anthropic
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "Content-Type": "application/json",
                "x-api-key": ANTHROPIC_KEY,
                "anthropic-version": "2023-06-01",
                "anthropic-beta": "web-search-2025-03-05",
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 1000,
                "system": system,
                "tools": [{"type": "web_search_20250305", "name": "web_search"}],
                "messages": messages,
            }
        )
    data = resp.json()

    # Extraer respuesta texto
    reply = ""
    for b in data.get("content", []):
        if b.get("type") == "text":
            reply += b["text"]

    # Guardar respuesta del agente
    if reply:
        guardar_mensaje("assistant", reply, session_id, db)
        guardar_decision(reply, db)

    # Generar aprendizaje si la conversación tiene 4+ turnos
    if len(messages) >= 4 and len(messages) % 4 == 0:
        await generar_aprendizaje(messages, db)

    return JSONResponse(content=data, status_code=resp.status_code)

@app.get("/memoria")
async def ver_memoria():
    """Endpoint para ver qué ha aprendido el agente."""
    db = get_db()
    try:
        aprendizajes = db.table("aprendizajes").select("*").order("created_at", desc=True).limit(20).execute()
        decisiones   = db.table("decisiones").select("*").order("created_at", desc=True).limit(20).execute()
        return {"aprendizajes": aprendizajes.data, "decisiones": decisiones.data}
    except Exception as e:
        return {"error": str(e)}
