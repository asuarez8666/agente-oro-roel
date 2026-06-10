from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import httpx, os, json, re

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

SUPA_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": "Bearer " + SUPABASE_KEY,
    "Content-Type": "application/json",
    "Prefer": "return=minimal"
}

BASE_CONTEXT = "Empresa de joyeria mayorista Roel Joyas. Operaciones en Colombia, Mexico y Chile. Compras tipicas menos de $10,000 USD. Unidad: GRAMOS. Monedas: USD y COP. Precio por gramo = precio onza troy / 31.1035."

SYSTEM_PROMPT = "Eres el agente de mercado de oro de Roel Joyas. " + BASE_CONTEXT + " Busca en la web el precio spot actual XAU/USD y tipo de cambio USD/COP. Reporta en USD/gramo y COP/gramo. Da senal clara: COMPRAR, ESPERAR o VENDER. Maximo 220 palabras. Siempre en espanol."


async def db_insert(table, data):
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            await c.post(SUPABASE_URL + "/rest/v1/" + table, headers=SUPA_HEADERS, json=data)
    except Exception as e:
        print("db_insert error: " + str(e))


async def db_select(table, order, limit):
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                SUPABASE_URL + "/rest/v1/" + table,
                headers=dict(list(SUPA_HEADERS.items()) + [("Accept", "application/json")]),
                params={"order": order + ".desc", "limit": limit}
            )
            if r.status_code == 200:
                return r.json()
    except Exception as e:
        print("db_select error: " + str(e))
    return []


async def cargar_memoria():
    try:
        ap = await db_select("aprendizajes", "created_at", 8)
        dec = await db_select("decisiones", "created_at", 5)
        mem = ""
        if ap:
            mem += "\nAPRENDIZAJES PREVIOS:\n"
            for a in ap:
                mem += "- [" + str(a.get("created_at", ""))[:10] + "] " + str(a.get("contenido", "")) + "\n"
        if dec:
            mem += "\nDECISIONES RECIENTES:\n"
            for d in dec:
                mem += "- [" + str(d.get("created_at", ""))[:10] + "] " + str(d.get("accion", "")) + " " + str(d.get("cantidad_gramos", "")) + "g\n"
        return mem if mem else "\n(Primera sesion)\n"
    except Exception as e:
        print("cargar_memoria error: " + str(e))
        return "\n(Memoria no disponible)\n"


async def guardar_decision(respuesta):
    try:
        match = re.search(r'\[DECISION:(.*?)\]', respuesta)
        if not match:
            return
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
        print("guardar_decision error: " + str(e))


async def generar_aprendizaje(messages):
    try:
        prompt = "Analiza esta conversacion y extrae maximo 2 aprendizajes utiles sobre el mercado del oro. Conversacion: " + json.dumps(messages[-4:], ensure_ascii=False) + "\nResponde SOLO con JSON: {\"aprendizajes\": [\"aprendizaje 1\"]}"
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(
                "https://api.anthropic.com/v1/messages",
                headers={"Content-Type": "application/json", "x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01"},
                json={"model": "claude-haiku-4-5-20251001", "max_tokens": 200, "messages": [{"role": "user", "content": prompt}]}
            )
        data = r.json()
        text = "".join(b["text"] for b in data.get("content", []) if b.get("type") == "text")
        m = re.search(r'\{[\s\S]*\}', text)
        if m:
            parsed = json.loads(m.group())
            for a in parsed.get("aprendizajes", []):
                if a.strip():
                    await db_insert("aprendizajes", {"contenido": a.strip()})
    except Exception as e:
        print("generar_aprendizaje error: " + str(e))


@app.get("/")
async def index():
    with open("index.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


@app.get("/ping")
async def ping():
    return {"status": "ok"}


@app.get("/memoria")
async def ver_memoria():
    ap = await db_select("aprendizajes", "created_at", 20)
    dec = await db_select("decisiones", "created_at", 20)
    return {"aprendizajes": ap, "decisiones": dec}


@app.post("/chat")
async def chat(request: Request):
    body = await request.json()
    messages = body.get("messages", [])
    session_id = body.get("session_id", "default")

    if messages and messages[-1].get("role") == "user":
        await db_insert("mensajes", {"session_id": session_id, "role": "user", "content": messages[-1]["content"][:2000]})

    memoria = await cargar_memoria()
    system = SYSTEM_PROMPT + "\n\n" + memoria

    async with httpx.AsyncClient(timeout=60) as c:
        resp = await c.post(
            "https://api.anthropic.com/v1/messages",
            headers={"Content-Type": "application/json", "x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01", "anthropic-beta": "web-search-2025-03-05"},
            json={"model": "claude-sonnet-4-20250514", "max_tokens": 1000, "system": system, "tools": [{"type": "web_search_20250305", "name": "web_search"}], "messages": messages}
        )

    data = resp.json()
    reply = "".join(b["text"] for b in data.get("content", []) if b.get("type") == "text")

    if reply:
        await db_insert("mensajes", {"session_id": session_id, "role": "assistant", "content": reply[:2000]})
        await guardar_decision(reply)

    if len(messages) >= 4 and len(messages) % 4 == 0:
        await generar_aprendizaje(messages)

    return JSONResponse(content=data, status_code=resp.status_code)
