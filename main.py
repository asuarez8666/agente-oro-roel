from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import httpx, os, json, re

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

SYSTEM_PROMPT = """Eres el agente de mercado de oro de Roel Joyas, empresa de joyeria mayorista en Colombia, Mexico y Chile.
Busca en la web el precio spot actual XAU/USD y tipo de cambio USD/COP.
Reporta en USD/gramo y COP/gramo. Precio por gramo = precio onza troy / 31.1035.
Da senal clara: COMPRAR, ESPERAR o VENDER. Maximo 220 palabras. Siempre en espanol."""


@app.get("/")
async def index():
    with open("index.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


@app.get("/ping")
async def ping():
    return {"status": "ok"}


@app.get("/memoria")
async def ver_memoria():
    try:
        headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": "Bearer " + SUPABASE_KEY,
            "Accept": "application/json"
        }
        async with httpx.AsyncClient(timeout=10) as c:
            ap = await c.get(SUPABASE_URL + "/rest/v1/aprendizajes?order=created_at.desc&limit=20", headers=headers)
            dec = await c.get(SUPABASE_URL + "/rest/v1/decisiones?order=created_at.desc&limit=20", headers=headers)
        return {
            "aprendizajes": ap.json() if ap.status_code == 200 else [],
            "decisiones": dec.json() if dec.status_code == 200 else []
        }
    except Exception as e:
        return {"aprendizajes": [], "decisiones": [], "error": str(e)}


@app.post("/chat")
async def chat(request: Request):
    try:
        body = await request.json()
        messages = body.get("messages", [])
        session_id = body.get("session_id", "default")

        # Guardar mensaje (sin bloquear si falla)
        try:
            if messages and messages[-1].get("role") == "user":
                headers = {
                    "apikey": SUPABASE_KEY,
                    "Authorization": "Bearer " + SUPABASE_KEY,
                    "Content-Type": "application/json",
                    "Prefer": "return=minimal"
                }
                async with httpx.AsyncClient(timeout=5) as c:
                    await c.post(
                        SUPABASE_URL + "/rest/v1/mensajes",
                        headers=headers,
                        json={"session_id": session_id, "role": "user", "content": messages[-1]["content"][:2000]}
                    )
        except:
            pass

        # Cargar memoria (sin bloquear si falla)
        memoria = ""
        try:
            headers = {
                "apikey": SUPABASE_KEY,
                "Authorization": "Bearer " + SUPABASE_KEY,
                "Accept": "application/json"
            }
            async with httpx.AsyncClient(timeout=5) as c:
                ap = await c.get(SUPABASE_URL + "/rest/v1/aprendizajes?order=created_at.desc&limit=5", headers=headers)
                dec = await c.get(SUPABASE_URL + "/rest/v1/decisiones?order=created_at.desc&limit=3", headers=headers)
            if ap.status_code == 200 and ap.json():
                memoria += "\nAPRENDIZAJES PREVIOS:\n"
                for a in ap.json():
                    memoria += "- " + str(a.get("contenido", "")) + "\n"
            if dec.status_code == 200 and dec.json():
                memoria += "\nDECISIONES RECIENTES:\n"
                for d in dec.json():
                    memoria += "- " + str(d.get("accion", "")) + " " + str(d.get("cantidad_gramos", "")) + "g\n"
        except:
            pass

        system = SYSTEM_PROMPT
        if memoria:
            system += "\n\nCONTEXTO PREVIO:" + memoria

        # Llamar a Anthropic
        async with httpx.AsyncClient(timeout=60) as c:
            resp = await c.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": ANTHROPIC_KEY,
                    "anthropic-version": "2023-06-01",
                    "anthropic-beta": "web-search-2025-03-05"
                },
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 1000,
                    "system": system,
                    "tools": [{"type": "web_search_20250305", "name": "web_search"}],
                    "messages": messages
                }
            )

        data = resp.json()

        # Guardar respuesta (sin bloquear si falla)
        try:
            reply = "".join(b["text"] for b in data.get("content", []) if b.get("type") == "text")
            if reply:
                headers = {
                    "apikey": SUPABASE_KEY,
                    "Authorization": "Bearer " + SUPABASE_KEY,
                    "Content-Type": "application/json",
                    "Prefer": "return=minimal"
                }
                async with httpx.AsyncClient(timeout=5) as c:
                    await c.post(
                        SUPABASE_URL + "/rest/v1/mensajes",
                        headers=headers,
                        json={"session_id": session_id, "role": "assistant", "content": reply[:2000]}
                    )
        except:
            pass

        return JSONResponse(content=data, status_code=resp.status_code)

    except Exception as e:
        print("chat error: " + str(e))
        return JSONResponse(content={"error": str(e)}, status_code=500)

