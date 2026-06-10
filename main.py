from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import httpx, os

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

SYSTEM_PROMPT = """Eres el agente de mercado de oro de Roel Joyas, empresa de joyeria mayorista en Colombia, Mexico y Chile.
Reporta precios en USD/gramo y COP/gramo. Precio por gramo = precio onza troy / 31.1035.
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
    return {"aprendizajes": [], "decisiones": []}

@app.post("/query")
async def query(request: Request):
    try:
        body = await request.json()
        messages = body.get("messages", [])
        async with httpx.AsyncClient(timeout=30) as c:
            resp = await c.post(
                "https://api.anthropic.com/v1/messages",
                headers={"Content-Type": "application/json", "x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01"},
                json={"model": "claude-sonnet-4-20250514", "max_tokens": 1000, "system": SYSTEM_PROMPT, "messages": messages}
            )
        return JSONResponse(content=resp.json(), status_code=resp.status_code)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)
