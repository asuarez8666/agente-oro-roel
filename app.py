import streamlit as st
import anthropic
import httpx
import os
import re
import json
from datetime import datetime

# ── Config ────────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Agente de Oro · Roel Joyas",
    page_icon="🥇",
    layout="centered"
)

# ── Autenticación ─────────────────────────────────────────────────────────────
APP_PASSWORD = st.secrets.get("APP_PASSWORD", "Roel2026")

if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=DM+Serif+Display&family=Sora:wght@400;500&display=swap');
    [data-testid="stAppViewContainer"]{background:#0F0E0B;}
    [data-testid="stHeader"]{background:transparent;}
    .block-container{padding-top:4rem !important; max-width:400px !important;}
    .login-logo{width:60px;height:60px;border-radius:50%;background:linear-gradient(135deg,#2563EB,#1D4ED8);display:flex;align-items:center;justify-content:center;font-family:'DM Serif Display',serif;font-size:24px;color:#fff;margin:0 auto 16px;}
    .login-title{font-family:'DM Serif Display',serif;font-size:26px;color:#F5EDD8;text-align:center;font-weight:400;}
    .login-sub{font-size:12px;color:#6B5E42;text-align:center;font-family:'DM Mono',monospace;letter-spacing:0.08em;text-transform:uppercase;margin-bottom:24px;}
    </style>
    """, unsafe_allow_html=True)
    st.markdown('<div class="login-logo">Au</div>', unsafe_allow_html=True)
    st.markdown('<div class="login-title">Agente de Oro</div>', unsafe_allow_html=True)
    st.markdown('<div class="login-sub">Roel Joyas · Acceso restringido</div>', unsafe_allow_html=True)
    pwd = st.text_input("Contraseña", type="password", placeholder="Ingresa la contraseña...")
    if st.button("Entrar", use_container_width=True):
        if pwd == APP_PASSWORD:
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("Contraseña incorrecta")
    st.stop()

ANTHROPIC_KEY = st.secrets.get("ANTHROPIC_API_KEY", os.environ.get("ANTHROPIC_API_KEY", ""))
SUPABASE_URL  = st.secrets.get("SUPABASE_URL",  os.environ.get("SUPABASE_URL", "")).rstrip("/")
SUPABASE_KEY  = st.secrets.get("SUPABASE_KEY",  os.environ.get("SUPABASE_KEY", ""))

SUPA_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": "Bearer " + SUPABASE_KEY,
    "Content-Type": "application/json",
    "Prefer": "return=minimal"
}

SYSTEM_PROMPT = """Eres el agente de mercado de oro de Roel Joyas, empresa de joyeria mayorista con operaciones en Colombia, Mexico y Chile.

CONTEXTO:
- El dueno/gerente usa este agente para decisiones de compra y venta de oro.
- Compras tipicas: menos de $10,000 USD por operacion.
- Unidad: GRAMOS. Monedas: USD y COP.
- Precio por gramo = precio onza troy / 31.1035.

INSTRUCCIONES:
1. Busca en la web el precio spot actual XAU/USD y tipo de cambio USD/COP antes de responder.
2. Reporta siempre en USD/gramo y COP/gramo.
3. Da senal clara: COMPRAR, ESPERAR o VENDER con razonamiento breve.
4. Si el usuario menciona una decision real (compro X gramos, vendio), agrega al final:
   [DECISION: accion=COMPRA|VENTA|ESPERA, cantidad_gramos=X, precio_usd_gramo=Y, notas=texto]
5. Maximo 220 palabras. Siempre en espanol."""

# ── Supabase helpers ──────────────────────────────────────────────────────────
def db_insert(table, data):
    try:
        httpx.post(SUPABASE_URL + "/rest/v1/" + table, headers=SUPA_HEADERS, json=data, timeout=5)
    except:
        pass

def db_select(table, order, limit):
    try:
        r = httpx.get(
            SUPABASE_URL + "/rest/v1/" + table,
            headers={**SUPA_HEADERS, "Accept": "application/json"},
            params={"order": order + ".desc", "limit": limit},
            timeout=5
        )
        if r.status_code == 200:
            return r.json()
    except:
        pass
    return []

def cargar_memoria():
    ap  = db_select("aprendizajes", "created_at", 8)
    dec = db_select("decisiones", "created_at", 5)
    mem = ""
    if ap:
        mem += "\nAPRENDIZAJES PREVIOS:\n"
        for a in ap:
            mem += f"- [{str(a.get('created_at',''))[:10]}] {a.get('contenido','')}\n"
    if dec:
        mem += "\nDECISIONES RECIENTES:\n"
        for d in dec:
            mem += f"- [{str(d.get('created_at',''))[:10]}] {d.get('accion','')} {d.get('cantidad_gramos','')}g a ${d.get('precio_usd_gramo','')}/g\n"
    return mem

def guardar_decision(respuesta):
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
        db_insert("decisiones", {
            "accion": partes.get("accion", ""),
            "cantidad_gramos": float(partes.get("cantidad_gramos", 0) or 0),
            "precio_usd_gramo": float(partes.get("precio_usd_gramo", 0) or 0),
            "notas": partes.get("notas", ""),
        })
    except:
        pass

def generar_aprendizaje(messages):
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
        prompt = f"Analiza esta conversacion y extrae maximo 2 aprendizajes utiles sobre el mercado del oro. Conversacion: {json.dumps(messages[-4:], ensure_ascii=False)}\nResponde SOLO con JSON: {{\"aprendizajes\": [\"aprendizaje 1\"]}}"
        resp = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}]
        )
        text = resp.content[0].text
        m = re.search(r'\{[\s\S]*\}', text)
        if m:
            parsed = json.loads(m.group())
            for a in parsed.get("aprendizajes", []):
                if a.strip():
                    db_insert("aprendizajes", {"contenido": a.strip()})
    except:
        pass

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Serif+Display&family=Sora:wght@300;400;500&family=DM+Mono:wght@400;500&display=swap');

[data-testid="stAppViewContainer"] { background: #0F0E0B; }
[data-testid="stHeader"] { background: transparent; }
.block-container { padding-top: 1rem !important; max-width: 780px !important; }

.header-wrap { display:flex; align-items:center; gap:14px; padding:16px 0 12px; border-bottom:1px solid rgba(37,99,235,0.25); margin-bottom:16px; }
.logo-circle { width:42px; height:42px; border-radius:50%; background:linear-gradient(135deg,#2563EB,#1D4ED8); display:flex; align-items:center; justify-content:center; font-family:'DM Serif Display',serif; font-size:17px; color:#fff; flex-shrink:0; }
.header-title { font-family:'DM Serif Display',serif; font-size:20px; color:#F5EDD8; font-weight:400; }
.header-sub { font-size:10px; color:#6B5E42; font-family:'DM Mono',monospace; letter-spacing:0.08em; text-transform:uppercase; }
.live-dot { width:8px; height:8px; border-radius:50%; background:#4CAF82; display:inline-block; margin-right:5px; }

.price-strip { display:grid; grid-template-columns:1fr 1fr; gap:1px; background:rgba(37,99,235,0.25); border-radius:10px; overflow:hidden; margin-bottom:14px; }
.price-cell { background:#1A1814; padding:12px 18px; }
.price-label { font-size:10px; color:#6B5E42; font-family:'DM Mono',monospace; text-transform:uppercase; letter-spacing:0.1em; margin-bottom:4px; }
.price-val { font-family:'DM Serif Display',serif; font-size:22px; color:#93C5FD; }
.price-gram { font-size:11px; color:#6B5E42; font-family:'DM Mono',monospace; margin-top:2px; }

.msg-user { background:#2563EB; color:#1A1200; border-radius:16px 4px 16px 16px; padding:12px 16px; margin:6px 0; font-size:14px; font-weight:500; margin-left:15%; }
.msg-agent { background:#2A2720; color:#F5EDD8; border-radius:4px 16px 16px 16px; padding:12px 16px; margin:6px 0; font-size:14px; border:1px solid rgba(37,99,235,0.25); margin-right:5%; }
.msg-agent b { color:#93C5FD; }
.msg-name { font-size:10px; color:#6B5E42; font-family:'DM Mono',monospace; margin-bottom:4px; }
.tag-up { background:rgba(76,175,130,.15); color:#4CAF82; border:1px solid rgba(76,175,130,.3); padding:2px 8px; border-radius:4px; font-size:11px; font-family:'DM Mono',monospace; }
.tag-down { background:rgba(224,92,75,.15); color:#E05C4B; border:1px solid rgba(224,92,75,.3); padding:2px 8px; border-radius:4px; font-size:11px; font-family:'DM Mono',monospace; }
.tag-neutral { background:rgba(37,99,235,.15); color:#2563EB; border:1px solid rgba(37,99,235,.4); padding:2px 8px; border-radius:4px; font-size:11px; font-family:'DM Mono',monospace; }

#MainMenu {visibility:hidden;} footer {visibility:hidden;} header {visibility:hidden;} [data-testid="stToolbar"]{display:none;} [data-testid="stDecoration"]{display:none;}
[data-testid="stChatInput"] textarea { background:#2A2720 !important; color:#F5EDD8 !important; border:1px solid rgba(37,99,235,0.4) !important; border-radius:14px !important; font-family:'Sora',sans-serif !important; }
[data-testid="stChatInput"] textarea:focus { border-color:#2563EB !important; }
button[kind="secondary"] { background:transparent !important; border:1px solid rgba(37,99,235,0.4) !important; color:#A8997A !important; border-radius:20px !important; font-size:12px !important; }
button[kind="secondary"]:hover { border-color:#2563EB !important; color:#93C5FD !important; }
</style>
""", unsafe_allow_html=True)

# ── Header ────────────────────────────────────────────────────────────────────
st.markdown("""
<div class="header-wrap">
  <div class="logo-circle">Au</div>
  <div>
    <div class="header-title">Agente de Oro</div>
    <div class="header-sub">Roel Joyas · Análisis de mercado</div>
  </div>
  <div style="margin-left:auto;font-size:12px;color:#6B5E42;font-family:'DM Mono',monospace;">
    <span class="live-dot"></span>EN VIVO
  </div>
</div>
""", unsafe_allow_html=True)

# ── Session state ─────────────────────────────────────────────────────────────
if "messages" not in st.session_state:
    st.session_state.messages = []
if "price_data" not in st.session_state:
    st.session_state.price_data = None

# ── Precio strip ──────────────────────────────────────────────────────────────
price_placeholder = st.empty()

def render_price_strip(data=None):
    if data:
        usd_oz = data.get("xau_usd", 0)
        tc     = data.get("usd_cop", 0)
        delta  = data.get("delta_pct", 0)
        usd_g  = usd_oz / 31.1035
        cop_g  = usd_g * tc
        d_color = "#4CAF82" if delta >= 0 else "#E05C4B"
        d_arrow = "↑" if delta >= 0 else "↓"
        price_placeholder.markdown(f"""
        <div class="price-strip">
          <div class="price-cell">
            <div class="price-label">XAU / USD · Onza Troy</div>
            <div class="price-val">${usd_oz:,.2f}</div>
            <div class="price-gram">${usd_g:.2f} USD / gramo</div>
            <div style="font-size:11px;color:{d_color};margin-top:2px;">{d_arrow} {'+' if delta>=0 else ''}{delta}% hoy</div>
          </div>
          <div class="price-cell">
            <div class="price-label">XAU / COP · Onza Troy</div>
            <div class="price-val">${round(usd_oz*tc/1000):,}K</div>
            <div class="price-gram">${round(cop_g):,} COP / gramo</div>
            <div style="font-size:11px;color:#A8997A;margin-top:2px;">TC: {round(tc):,} COP/USD</div>
          </div>
        </div>
        """, unsafe_allow_html=True)
    else:
        price_placeholder.markdown("""
        <div class="price-strip">
          <div class="price-cell"><div class="price-label">XAU / USD · Onza Troy</div><div class="price-val" style="color:#6B5E42">— —</div><div class="price-gram">Cargando...</div></div>
          <div class="price-cell"><div class="price-label">XAU / COP · Onza Troy</div><div class="price-val" style="color:#6B5E42">— —</div><div class="price-gram">TC USD/COP: —</div></div>
        </div>
        """, unsafe_allow_html=True)

render_price_strip(st.session_state.price_data)

# ── Quick actions ─────────────────────────────────────────────────────────────
cols = st.columns(5)
quick_questions = [
    ("¿Compro hoy?",     "¿Debería comprar oro hoy o esperar? Dame tu señal clara."),
    ("Precio por gramo", "¿Cuánto vale 1 gramo de oro ahora en USD y en COP?"),
    ("¿Por qué se mueve?","¿Qué está moviendo el precio del oro hoy?"),
    ("Resumen semanal",  "Dame un resumen del mercado del oro esta semana."),
    ("Perspectiva",      "¿Qué se espera del oro en los próximos días?"),
]
quick_input = None
for i, (label, question) in enumerate(quick_questions):
    if cols[i].button(label, use_container_width=True):
        quick_input = question

# ── Historial ────────────────────────────────────────────────────────────────
def format_agent_text(text):
    text = re.sub(r'\[DECISION:.*?\]', '', text, flags=re.DOTALL)
    text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
    text = text.replace('COMPRAR', '<span class="tag-up">▲ COMPRAR</span>')
    text = text.replace('VENDER',  '<span class="tag-down">▼ VENDER</span>')
    text = text.replace('ESPERAR', '<span class="tag-neutral">◆ ESPERAR</span>')
    text = text.replace('\n', '<br>')
    return text

chat_container = st.container()
with chat_container:
    if not st.session_state.messages:
        st.markdown("""
        <div class="msg-agent">
          <div class="msg-name">AGENTE DE ORO</div>
          Hola 👋 Soy tu agente de mercado del oro para <b>Roel Joyas</b>.<br><br>
          Puedo decirte el precio actual en <b>USD y COP</b>, si conviene comprar o esperar, y cualquier análisis que necesites.<br><br>
          ¿En qué te ayudo hoy?
        </div>
        """, unsafe_allow_html=True)
    for msg in st.session_state.messages:
        if msg["role"] == "user":
            st.markdown(f'<div class="msg-user">{msg["content"]}</div>', unsafe_allow_html=True)
        else:
            st.markdown(f'<div class="msg-agent"><div class="msg-name">AGENTE DE ORO</div>{format_agent_text(msg["content"])}</div>', unsafe_allow_html=True)

# ── Memoria sidebar ───────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### 🧠 Memoria del agente")
    if st.button("Actualizar memoria"):
        ap  = db_select("aprendizajes", "created_at", 10)
        dec = db_select("decisiones",   "created_at", 10)
        if ap:
            st.markdown("**📚 Aprendizajes**")
            for a in ap:
                st.markdown(f"- {a.get('contenido','')}")
        else:
            st.caption("Sin aprendizajes aún")
        if dec:
            st.markdown("**💼 Decisiones**")
            for d in dec:
                accion = d.get('accion','')
                color = "🟢" if accion == "COMPRA" else "🔴" if accion == "VENTA" else "🟡"
                st.markdown(f"{color} {accion} · {d.get('cantidad_gramos','')}g · ${d.get('precio_usd_gramo','')}/g")
        else:
            st.caption("Sin decisiones registradas")

# ── Enviar mensaje ─────────────────────────────────────────────────────────────
user_input = st.chat_input("Pregúntame sobre el precio del oro, si conviene comprar, tendencias...")
if quick_input:
    user_input = quick_input

if user_input:
    st.session_state.messages.append({"role": "user", "content": user_input})
    st.markdown(f'<div class="msg-user">{user_input}</div>', unsafe_allow_html=True)

    with st.spinner("Buscando información..."):
        try:
            memoria = cargar_memoria()
            system  = SYSTEM_PROMPT + ("\n\nCONTEXTO PREVIO:" + memoria if memoria else "")
            client  = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

            # Limitar historial a ultimos 6 mensajes para ahorrar tokens
            api_messages = [{"role": m["role"], "content": m["content"]} for m in st.session_state.messages[-6:]]

            resp = client.messages.create(
                model="claude-sonnet-4-5",
                max_tokens=600,
                system=system,
                tools=[{"type": "web_search_20250305", "name": "web_search"}],
                extra_headers={"anthropic-beta": "web-search-2025-03-05"},
                messages=api_messages
            )

            reply = "".join(b.text for b in resp.content if hasattr(b, "text"))

            # Actualizar precio strip si hay datos
            price_match = re.search(r'"xau_usd"\s*:\s*([\d.]+)', reply)
            cop_match   = re.search(r'"usd_cop"\s*:\s*([\d.]+)', reply)
            delta_match = re.search(r'"delta_pct"\s*:\s*([-\d.]+)', reply)
            if price_match and cop_match:
                st.session_state.price_data = {
                    "xau_usd":   float(price_match.group(1)),
                    "usd_cop":   float(cop_match.group(1)),
                    "delta_pct": float(delta_match.group(1)) if delta_match else 0
                }
                render_price_strip(st.session_state.price_data)

            st.session_state.messages.append({"role": "assistant", "content": reply})
            st.markdown(f'<div class="msg-agent"><div class="msg-name">AGENTE DE ORO</div>{format_agent_text(reply)}</div>', unsafe_allow_html=True)

            # Guardar en Supabase
            db_insert("mensajes", {"session_id": "streamlit", "role": "user", "content": user_input[:2000]})
            db_insert("mensajes", {"session_id": "streamlit", "role": "assistant", "content": reply[:2000]})
            guardar_decision(reply)

            if len(st.session_state.messages) >= 4 and len(st.session_state.messages) % 4 == 0:
                generar_aprendizaje(api_messages)

        except Exception as e:
            st.error(f"Error: {str(e)}")
