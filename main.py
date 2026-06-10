from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import httpx, os, json, re
from datetime import datetime

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
SUPABASE_URL  = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY  = os.environ.get("SUPABASE_KEY", "")

SUPA_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=minimal"
}

# ── Supabase helpers (httpx directo, sin librería) ─────────────────────────────
async def db_insert(table: str, data: dict):
    async with httpx.AsyncClient(timeout=10) as c:
        await c.post(f"{SUPABASE_URL}/rest/v1/{table}", headers=SUPA_HEADERS, json=data)

async def db_select(table: str, order: str, limit: int) -> list:
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(
            f"{SUPABASE_URL}/rest/v1/{table}",
            headers={**SUPA_HEADERS, "Accept": "application/json"},
            params={"order": f"{order}.desc", "limit": limit}
        )
        return r.json() if r.status_code == 200 else []

# ── Contexto base Nivel 1 ─────────────────────────────────────────────────────
BASE_CONTEXT = """
CONTEXTO FIJO DE ROEL JOYAS:
- Empresa de joyería mayorista con operaciones en Colombia, México y Chile.
- El dueño/gerente usa este agente para tomar decisiones de compra y venta de oro.
- Compras típicas: menos de $10,000 USD por operación.
- Unidad de trabajo: GRAMOS (nunca onzas ni kilos al reportar).
- Monedas: USD (principal) y COP (Colombia).
- Decisiones clave: cuándo comprar, a qué precio, cuándo vender o liquidar.
- Precio por gramo = precio onza troy / 31.1035.
"""

SYSTEM_TEMPLATE = """Eres el agente de mercado de oro de Roel Joyas. Apoyas al dueño/gerente a tomar decisiones de compra y venta.

{base_context}

{memoria_dinamica}

INSTRUCCIONES:
1. Busca en la web el precio spot actual XAU/USD y tipo de cambio USD/COP antes de responder sobre precios.
2. Reporta siempre en USD/gramo y COP/gramo.
3. Da señal clara: COMPRAR, ESPERAR o VENDER.
4. Si el usuario menciona una decisión real (compró X gramos, vendió, esperó), agrega al final:
   [DECISION: accion=COMPRA|VENTA|ESPERA, cantidad_gramos=X, precio_usd_gramo=Y, notas=texto]
   Solo si hay decisión real. No la inventes.
5. Máximo 220 palabras. Siempre en español."""

# ── Memoria dinámica Nivel 2 ───────────────────────────────────────────────────
async def cargar_memoria() -> str:
    try:
        aprendizajes = await db_select("aprendizajes", "created_at", 8)
        decisiones   = await db_select("decisiones", "created_at", 5)
        memoria = ""
        if aprendizajes:
            memoria += "\nAPRENDIZAJES PREVIOS:\n"
            for a in aprendizajes:
                fecha = str(a.get("created_at",""))[:10]
                memoria += f"- [{fecha}] {a.get('contenido','')}\n"
        if decisiones:
            memoria += "\nDECISIONES RECIENTES:\n"
            for d in decisiones:
                fecha = str(d.get("created_at",""))[:10]
                memoria += f"- [{fecha}] {d.get('accion','')} {d.get('cantidad_gramos','')}g a ${d.get('precio_usd_gramo','')}/g — {d.get('notas','')}\n"
        return memoria if memoria else "\n(Primera sesión — sin historial previo.)\n"
    except:
        return "\n(Memoria no disponible.)\n"

async def guardar_decision(respuesta: str):
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
        await db_insert("decisiones", {
            "accion": partes.get("accion", ""),
            "cantidad_gramos": float(partes.get("cantidad_gramos", 0) or 0),
            "precio_usd_gramo": float(partes.get("precio_usd_gramo", 0) or 0),
            "notas": partes.get("notas", ""),
        })
    except Exception as e:
        print(f"Error guardando decisión: {e}")

async def generar_aprendizaje(messages: list):
    try:
        prompt = f"""Analiza esta conversación sobre el mercado del oro y extrae máximo 3 aprendizajes concretos útiles para el futuro.

Conversación: {json.dumps(messages[-6:], ensure_ascii=False)}

Responde SOLO con JSON sin texto adicional ni backticks:
{{"aprendizajes": ["aprendizaje 1", "aprendizaje 2"]}}"""

        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": ANTHROPIC_KEY,
                    "anthropic-version": "2023-06-01",
                },
                json={"model": "claude-haiku-4-5-20251001", "max_tokens": 300,
                      "messages": [{"role": "user", "content": prompt}]}
            )
        data = r.json()
        text = "".join(b["text"] for b in data.get("content", []) if b.get("type") == "text")
        m = re.search(r'\{[\s\S]*\}', text)
        if m:
            parsed = json.loads(m.group())
            for ap in parsed.get("aprendizajes", []):
                if ap.strip():
                    await db_insert("aprendizajes", {"contenido": ap.strip()})
    except Exception as e:
        print(f"Error generando aprendizaje: {e}")

# ── Endpoints ──────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def index():
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()

@app.post("/api")
async def proxy(request: Request):
    body = await request.json()
    messages   = body.get("messages", [])
    session_id = body.get("session_id", "default")

    # Guardar mensaje usuario
    if messages and messages[-1].get("role") == "user":
        await db_insert("mensajes", {
            "session_id": session_id,
            "role": "user",
            "content": messages[-1]["content"][:2000]
        })

    memoria  = await cargar_memoria()
    system   = SYSTEM_TEMPLATE.format(base_context=BASE_CONTEXT, memoria_dinamica=memoria)

    async with httpx.AsyncClient(timeout=60) as c:
        resp = await c.post(
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

    # Extraer reply y guardar
    reply = "".join(b["text"] for b in data.get("content", []) if b.get("type") == "text")
    if reply:
        await db_insert("mensajes", {"session_id": session_id, "role": "assistant", "content": reply[:2000]})
        await guardar_decision(reply)

    # Aprendizaje cada 4 turnos
    if len(messages) >= 4 and len(messages) % 4 == 0:
        await generar_aprendizaje(messages)

    return JSONResponse(content=data, status_code=resp.status_code)

@app.get("/memoria")
async def ver_memoria():
    try:
        aprendizajes = await db_select("aprendizajes", "created_at", 20)
        decisiones   = await db_select("decisiones", "created_at", 20)
        return {"aprendizajes": aprendizajes, "decisiones": decisiones}
    except Exception as e:
        return {"error": str(e)}
