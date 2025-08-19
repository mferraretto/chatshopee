# app_ui.py
# --- Força event loop correto no Windows (necessário para subprocess do Playwright) ---
import sys, asyncio
if sys.platform.startswith("win"):
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except Exception:
        pass
# ----------------------------------------------------------------------

import base64, json, time, os, re
from pathlib import Path
from typing import Optional, Set
from collections import deque

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Form, HTTPException, Response
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Template

from src.duoke import DuokeBot
from src.config import settings
from src.classifier import decide_reply
from src.rules import load_rules, save_rules

# ===== Estado global simples =====
RUNNING: bool = False
LAST_ERR: Optional[str] = None
LOGS = deque(maxlen=4000)

def log(line: str):
    s = f"[{time.strftime('%H:%M:%S')}] {line}"
    LOGS.append(s)
    print(s)

# ===== Arquivo de sessão do Duoke (Playwright) =====
STATE_PATH = Path("storage_state.json")

def duoke_is_connected() -> bool:
    return STATE_PATH.exists() and STATE_PATH.stat().st_size > 10  # heurística simples

# ===== HTML (UI single-file com tabs) =====
HTML = Template(r"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>Duoke Console</title>
  <style>
    :root { --bg:#0b0b0c; --fg:#fff; --mut:#b8b8b8; --card:#141416; --br:#2a2b31; --acc:#6ee7b7;}
    body { background:var(--bg); color:var(--fg); font-family: ui-sans-serif, system-ui, Arial; margin:0; }
    header { padding:16px 24px; border-bottom:1px solid var(--br); display:flex; align-items:center; gap:14px; flex-wrap:wrap;}
    .pill{border:1px solid var(--br); border-radius:999px; padding:4px 10px; color:var(--mut);}
    .tabs { display:flex; gap:10px; padding:10px 24px; border-bottom:1px solid var(--br);}
    .tabs a { text-decoration:none; color:var(--mut); padding:10px 12px; border-radius:10px; }
    .tabs a.active { background:var(--card); color:var(--fg); border:1px solid var(--br); }
    .wrap { padding:16px 24px; }
    .grid { display:grid; grid-template-columns: 1.3fr .9fr; gap:16px; }
    .card { background:var(--card); border:1px solid var(--br); border-radius:14px; padding:12px; }
    .row  { display:flex; gap:10px; align-items:center; flex-wrap:wrap;}
    button { background:var(--fg); color:#111; border:none; border-radius:10px; padding:10px 14px; cursor:pointer; }
    button.secondary { background:transparent; color:var(--fg); border:1px solid var(--br); }
    button[disabled]{ opacity:.5; cursor:not-allowed;}
    #screen { width:100%; aspect-ratio: 16 / 10; background:#000; border-radius:10px; object-fit:contain; }
    #log { height:220px; overflow:auto; font-family:ui-monospace,monospace; background:#0e0e10; border:1px solid var(--br); border-radius:10px; padding:10px; white-space:pre-wrap;}
    textarea,input,select { background:#0e0e10; color:var(--fg); border:1px solid var(--br); border-radius:10px; padding:10px; }
    input[type="email"],input[type="password"]{ width:280px; }
    table { width:100%; border-collapse:collapse; }
    th, td { border-bottom:1px solid var(--br); padding:8px; text-align:left; color:var(--mut);}
    .msg { border:1px solid var(--br); border-radius:10px; padding:8px; margin:6px 0; }
    .role-buyer { border-left:4px solid #60a5fa; }
    .role-seller { border-left:4px solid #a78bfa; }
    small.mut { color:var(--mut); }
  </style>
</head>
<body>
  <header>
    <strong>Duoke Console</strong>
    <span class="pill">Status: <span id="status">{{ "RUNNING" if running else "IDLE" }}</span></span>
    <span class="pill">Duoke: <span id="duokeStatus">{{ "Conectado" if duoke_connected else "Desconectado" }}</span></span>
  </header>

  <nav class="tabs">
    <a href="#ativo" class="active" id="tab-ativo">Ativo</a>
    <a href="#config" id="tab-config">Configurações</a>
    <a href="#regras" id="tab-regras">Regras</a>
  </nav>

  <main class="wrap">

    <!-- ABA ATIVO -->
    <section id="pane-ativo">
      <div class="grid">
        <div class="card">
          <div class="row" style="justify-content:space-between;">
            <div class="row">
              <form method="post" action="/start"><button id="btnStart" {{ "disabled" if running else "" }}>▶ Iniciar</button></form>
              <form method="post" action="/stop"><button id="btnStop" class="secondary" {{ "" if running else "disabled" }}>■ Parar</button></form>
              <form method="post" action="/run-once"><button class="secondary" {{ "disabled" if running else "" }}>Run once</button></form>
            </div>
            <small class="mut">Espelho do navegador</small>
          </div>
          <img id="screen" alt="browser mirror"/>
        </div>

        <div class="card">
          <h3 style="margin-top:0;">Leitura & Resposta</h3>
          <div id="reading"></div>
          <label style="display:block;margin-top:8px;">Resposta sugerida</label>
          <textarea id="proposed" rows="6" style="width:100%;"></textarea>
          <div class="row" style="margin-top:8px;">
            <button id="sendBtn">Enviar</button>
            <button id="skipBtn" class="secondary">Pular</button>
          </div>

          <div class="row" style="margin-top:8px;">
            <button id="btnCloseModal" class="secondary">Fechar modal</button>

            <input id="codeInput" type="text" placeholder="Código de verificação" style="width:180px;">
            <button id="btnSendCode">Enviar código</button>
          </div>

          <h4 style="margin-top:16px;">Conectar ao Duoke</h4>
          <p class="mut" style="margin-top:0">Faça login aqui para salvar a sessão (cookies) como <code>storage_state.json</code>. O bot reutiliza essa sessão automaticamente.</p>
          <form id="duoke-connect" onsubmit="return false;" style="display:flex; flex-direction:column; gap:8px; max-width:280px;">
            <input name="email" type="email" placeholder="Email Duoke" required />
            <input name="password" type="password" placeholder="Senha Duoke" required />
            <input name="code" type="text" placeholder="Código de verificação" />
            <div class="row">
              <button id="btnDuokeConnect" type="submit">Conectar ao Duoke</button>
              <button id="btnDuokeDisconnect" type="button" class="secondary">Desconectar</button>
            </div>
          </form>
          <small id="duokeHint" class="mut"></small>

          <h4>Logs</h4>
          <div id="log"></div>
          <small class="mut">Dica: mantenha esta aba aberta para não derrubar o WebSocket atrás de proxies.</small>
        </div>
      </div>
    </section>

    <!-- ABA CONFIG -->
    <section id="pane-config" style="display:none;">
      <div class="card" style="margin-bottom:16px;">
        <h3>Configurações</h3>
        <form method="post" action="/save-settings" class="row">
          <label>Max conversations</label><input name="max_conversations" type="number" min="0" value="{{ max_conv }}">
          <label>History depth</label><input name="history_depth" type="number" min="1" value="{{ depth }}">
          <label>Delay ações (s)</label><input name="delay_between_actions" type="number" step="0.1" min="0" value="{{ delay }}">
          <label>Selector campo texto</label><input name="input_selector" type="text" value="{{ input_sel }}">
          <button>Salvar</button>
        </form>
      </div>
    </section>

    <!-- ABA REGRAS -->
    <section id="pane-regras" style="display:none;">
      <div class="card">
        <h3>Regras</h3>
        <table>
          <thead><tr><th>Ativa</th><th>ID</th><th>Match (any_contains)</th><th>Ação</th><th>Resposta</th></tr></thead>
          <tbody id="rulesBody"></tbody>
        </table>
        <h4>Criar/Atualizar</h4>
        <form method="post" action="/save-rule">
          <div class="row">
            <label>ID</label><input name="id" type="text" required>
            <label>Ativa</label>
            <select name="active"><option value="true">true</option><option value="false">false</option></select>
            <label>Ação</label>
            <select name="action"><option value="">reply</option><option value="skip">skip</option></select>
          </div>
          <label>any_contains (separado por vírgula)</label>
          <input name="any_contains" type="text" style="width:100%;" placeholder="quebrado, faltou, não veio">
          <label>Resposta (se ação = reply)</label>
          <textarea name="reply" rows="5" style="width:100%;"></textarea>
          <div class="row"><button>Salvar regra</button><a href="/reload-rules" class="secondary" style="text-decoration:none;padding:10px 14px;border:1px solid var(--br);">Recarregar do arquivo</a></div>
        </form>
      </div>
    </section>

  </main>

<script>
const screen = document.getElementById('screen');
const reading = document.getElementById('reading');
const proposed = document.getElementById('proposed');
const logEl = document.getElementById('log');
const statusEl = document.getElementById('status');
const duokeStatusEl = document.getElementById('duokeStatus');
const duokeHint = document.getElementById('duokeHint');

screen.addEventListener('click', async ev => {
  const rect = screen.getBoundingClientRect();
  const scaleX = screen.naturalWidth / rect.width;
  const scaleY = screen.naturalHeight / rect.height;
  const x = ev.offsetX * scaleX;
  const y = ev.offsetY * scaleY;
  try {
    await fetch('/action/mouse-click', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ x, y })
    });
  } catch (e) {
    console.error('mouse click failed', e);
  }
});

function switchTab(hash) {
  document.querySelectorAll('.tabs a').forEach(a => a.classList.remove('active'));
  document.querySelectorAll('main section').forEach(s => s.style.display='none');
  const tab = document.getElementById('tab-'+hash);
  const pane = document.getElementById('pane-'+hash);
  if (tab && pane) { tab.classList.add('active'); pane.style.display='block'; }
}
window.addEventListener('hashchange', () => switchTab(location.hash.slice(1) || 'ativo'));
switchTab(location.hash.slice(1) || 'ativo');

async function loadRules() {
  const data = await fetch('/rules').then(r=>r.json());
  const tbody = document.getElementById('rulesBody');
  tbody.innerHTML = '';
  data.forEach(r => {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td>${r.active}</td><td>${r.id}</td><td>${(r.match?.any_contains||[]).join(', ')}</td><td>${r.action||'reply'}</td><td>${(r.reply||'').slice(0,80)}${(r.reply||'').length>80?'…':''}</td>`;
    tbody.appendChild(tr);
  })
}
loadRules();

let ws;
function connectWS(){
  const scheme = (location.protocol === 'https:') ? 'wss' : 'ws';
  ws = new WebSocket(`${scheme}://${location.host}/ws`);
  ws.onopen = () => {
    setInterval(() => { try { ws.send('ping'); } catch(e){} }, 20000);
  };
  ws.onmessage = (ev) => {
    const data = JSON.parse(ev.data);
    if (data.screen) {
      screen.src = "data:image/png;base64," + data.screen;
    }
    if (data.snapshot) {
      const s = data.snapshot;
      reading.innerHTML = '';
      (s.reading || []).forEach(pair => {
        const d = document.createElement('div');
        d.className = 'msg ' + (pair[0]==='buyer'?'role-buyer':'role-seller');
        d.textContent = pair[1];
        reading.appendChild(d);
      });
      if (s.proposed !== undefined && document.activeElement !== proposed) {
        proposed.value = s.proposed || '';
      }
      if (typeof s.running === 'boolean') {
        statusEl.textContent = s.running ? 'RUNNING' : 'IDLE';
      }
    }
    if (data.logline) {
      const needScroll = (logEl.scrollTop + logEl.clientHeight + 10) >= logEl.scrollHeight;
      logEl.textContent += (logEl.textContent ? '\n' : '') + data.logline;
      if (needScroll) logEl.scrollTop = logEl.scrollHeight;
    }
  }
  ws.onclose = () => setTimeout(connectWS, 2000);
}
connectWS();

document.getElementById('sendBtn').onclick = async () => {
  await fetch('/action/send', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({text: proposed.value})});
}
document.getElementById('skipBtn').onclick = async () => {
  await fetch('/action/skip', {method:'POST'});
}

document.getElementById('btnCloseModal').onclick = async () => {
  await fetch('/action/close-modal', {method:'POST'});
};

document.getElementById('btnSendCode').onclick = async () => {
  const code = (document.getElementById('codeInput').value || '').trim();
  if (!code) { alert('Digite o código.'); return; }
  await fetch('/action/submit-code', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ code })
  });
};

// ====== Duoke: conectar / desconectar / status ======
async function refreshDuokeStatus(){
  try{
    const r = await fetch('/duoke/status');
    const j = await r.json();
    duokeStatusEl.textContent = j.connected ? 'Conectado' : 'Desconectado';
    duokeHint.textContent = j.connected ? 'Sessão salva. Você pode iniciar o bot.' : 'Conecte-se ao Duoke para o bot conseguir ler as conversas.';
  }catch(e){
    duokeStatusEl.textContent = 'Desconhecido';
  }
}
refreshDuokeStatus();

document.getElementById('duoke-connect').addEventListener('submit', async (e)=>{
  e.preventDefault();
  const fd = new FormData(e.target);
  const btn = document.getElementById('btnDuokeConnect');
  btn.disabled = true;
  btn.textContent = 'Conectando...';
  try{
    const res = await fetch('/duoke/connect', { method:'POST', body: fd });
    if(!res.ok){ const t = await res.text(); alert('Falha ao conectar: ' + t); }
    else { alert('Duoke conectado!'); }
  }catch(err){
    alert('Erro: ' + err);
  }finally{
    btn.disabled = false;
    btn.textContent = 'Conectar ao Duoke';
    refreshDiokeStatus = null; // noop
    await refreshDuokeStatus();
  }
});

document.getElementById('btnDuokeDisconnect').addEventListener('click', async ()=>{
  if(!confirm('Remover sessão do Duoke deste servidor?')) return;
  const btn = document.getElementById('btnDuokeDisconnect');
  btn.disabled = true;
  try{
    const res = await fetch('/duoke/connect', { method:'DELETE' });
    if(!res.ok){ const t = await res.text(); alert('Falha ao desconectar: ' + t); }
    else { alert('Sessão removida.'); }
  }finally{
    btn.disabled = false;
    await refreshDuokeStatus();
  }
});
</script>
</body>
</html>
""")

app = FastAPI()


@app.head("/")
async def root_head() -> Response:
    """Simple HEAD handler for platform health checks."""
    return Response(status_code=200)


@app.get("/healthz")
async def health_check() -> dict:
    """Health check endpoint used by deployment platforms."""
    return {"status": "ok"}

# Monta /static somente se a pasta existir (evita erro em ambientes sem assets)
static_dir = Path("static")
if static_dir.exists() and static_dir.is_dir():
    app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/", response_class=HTMLResponse)
async def index():
    # Lê selectors.json se houver caminho no settings; cai num default robusto
    input_default = "textarea, [contenteditable='true']"
    try:
        if getattr(settings, "selectors_path", None):
            sel = json.loads(settings.selectors_path.read_text(encoding="utf-8"))
            input_default = sel.get("input_textarea", input_default)
    except Exception as e:
        log(f"[UI] aviso ao ler selectors.json: {type(e).__name__}: {e!r}")

    return HTML.render(
        running=RUNNING,
        duoke_connected=duoke_is_connected(),
        max_conv=(settings.max_conversations or 0),
        depth=(settings.history_depth or 5),
        delay=(settings.delay_between_actions or 1.0),
        input_sel=input_default
    )

@app.get("/rules")
async def rules():
    return JSONResponse(load_rules())

@app.get("/reload-rules")
async def reload_rules():
    return RedirectResponse("/", status_code=303)

@app.post("/save-rule")
async def save_rule(
    id: str = Form(...),
    active: str = Form("true"),
    action: str = Form(""),
    any_contains: str = Form(""),
    reply: str = Form("")
):
    rules = load_rules()
    found = None
    for r in rules:
        if r.get("id") == id:
            found = r
            break
    payload = {
        "id": id,
        "active": active.lower() == "true",
        "match": {"any_contains": [s.strip() for s in any_contains.split(",") if s.strip()]},
    }
    if action:
        payload["action"] = action
    if reply:
        payload["reply"] = reply
    if found:
        rules = [payload if r.get("id") == id else r for r in rules]
    else:
        rules.append(payload)
    save_rules(rules)
    return RedirectResponse("/", status_code=303)

@app.post("/save-settings")
async def save_settings(
    max_conversations: int = Form(...),
    history_depth: int = Form(...),
    delay_between_actions: float = Form(...),
    input_selector: str = Form(...)
):
    # Atualiza em memória
    settings.max_conversations = max_conversations
    settings.history_depth = history_depth
    settings.delay_between_actions = delay_between_actions
    # Atualiza selectors.json se existir
    try:
        sel_path = getattr(settings, "selectors_path", None)
        if sel_path:
            sel = json.loads(sel_path.read_text(encoding="utf-8"))
            sel["input_textarea"] = input_selector
            sel_path.write_text(json.dumps(sel, ensure_ascii=False, indent=2), encoding="utf-8")
            log("[UI] selectors.json atualizado (input_textarea).")
    except Exception as e:
        log(f"[UI] falha ao atualizar selectors.json: {type(e).__name__}: {e!r}")
    return RedirectResponse("/", status_code=303)

# ===== Endpoints de Conexão Duoke (Playwright) =====
@app.get("/duoke/status")
async def duoke_status():
    return {"connected": duoke_is_connected()}

@app.delete("/duoke/connect")
async def duoke_disconnect():
    try:
        if STATE_PATH.exists():
            STATE_PATH.unlink()
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, f"Falha ao remover sessão: {e}")

@app.post("/duoke/connect")
async def duoke_connect(email: str = Form(...), password: str = Form(...), code: str = Form("")):
    """
    Faz login no Duoke com Playwright headless e persiste cookies em storage_state.json.
    """
    # Import local para não exigir Playwright até alguém usar este endpoint
    try:
        from playwright.async_api import async_playwright, TimeoutError as PWTimeoutError
    except Exception as e:
        raise HTTPException(500, f"Playwright não instalado/configurado: {e}")

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
            ctx = await browser.new_context()
            page = await ctx.new_page()

            await page.goto(
                "https://www.duoke.com/",
                wait_until="domcontentloaded",
                timeout=settings.goto_timeout_ms,
            )

            # Fecha popup "Your login has expired" se aparecer
            try:
                await page.get_by_role("button", name="Confirm").click(timeout=2000)
            except Exception:
                pass

            # Seletores comuns; ajuste se o Duoke mudar
            email_sel = "input[type='email'], input[placeholder*='mail' i]"
            pass_sel  = "input[type='password'], input[placeholder*='senha' i], input[placeholder*='password' i]"

            # Se não houver campo de email, pode já estar logado
            if await page.locator(email_sel).count() == 0:
                # Garante state
                await ctx.storage_state(path=str(STATE_PATH))
                await browser.close()
                return {"ok": True, "already": True}

            await page.fill(email_sel, email)
            await page.fill(pass_sel, password)
            if code:
                code_sel = "input[name*='code' i], input[placeholder*='code' i], input[placeholder*='verification' i], input[type='tel']"
                try:
                    if await page.locator(code_sel).count() > 0:
                        await page.fill(code_sel, code)
                except Exception:
                    pass

            # Tenta botão Login por role/name
            try:
                await page.get_by_role("button", name=re.compile(r"(login|entrar|sign\s*in|iniciar\s*sess[aã]o)", re.I)).click(timeout=3000)
            except Exception:
                # Fallback por texto parcial
                btn = page.locator("button:has-text('Login'), button:has-text('Entrar'), button:has-text('Iniciar sessão')")
                if await btn.count() > 0:
                    await btn.first.click()
                else:
                    raise HTTPException(400, "Botão de login não encontrado")

            # Aguarda pós-login; ajuste se houver redirecionamento específico
            try:
                await page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass

            # Persistir sessão
            await ctx.storage_state(path=str(STATE_PATH))
            await browser.close()

        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Falha no login: {e}")

# ===== Streaming via WebSocket =====
CLIENTS: Set[WebSocket] = set()
def ws_broadcast(payload: dict):
    data = json.dumps(payload, ensure_ascii=False)
    for ws in list(CLIENTS):
        try:
            asyncio.create_task(ws.send_text(data))
        except Exception:
            CLIENTS.discard(ws)

@app.websocket("/ws")
async def ws(ws: WebSocket):
    await ws.accept()
    CLIENTS.add(ws)
    # Manda histórico de logs ao conectar
    try:
        for line in list(LOGS):
            await ws.send_text(json.dumps({"logline": line}, ensure_ascii=False))
    except Exception:
        pass
    ws_broadcast({"logline":"[UI] Conectado."})
    try:
        while True:
            # Mantém WS aberto; cliente envia 'ping' periódico
            await ws.receive_text()
    except WebSocketDisconnect:
        CLIENTS.discard(ws)
    except Exception:
        CLIENTS.discard(ws)

# ===== Bot Runner =====
_task: Optional[asyncio.Task] = None
_bot: Optional[DuokeBot] = None  # referência ao bot para espelho/ações

async def _mirror_loop():
    # Espelha a aba atual do Playwright
    while RUNNING and _bot:
        try:
            page = getattr(_bot, "current_page", None)
            if page:
                buf = await page.screenshot(full_page=False, type="png")
                ws_broadcast({"screen": base64.b64encode(buf).decode("ascii")})
        except Exception as e:
            log(f"[MIRROR] erro screenshot: {type(e).__name__}: {e!r}")
        # Throttle para reduzir uso de CPU/Rede
        await asyncio.sleep(2.5)

async def _run_cycle(run_once: bool):
    # run_once=True executa uma varredura; False mantém laço infinito
    global RUNNING, LAST_ERR, _task, _bot
    RUNNING = True
    LAST_ERR = None
    _bot = DuokeBot()

    # Hook para UI ver o que foi lido e a resposta sugerida
    async def hook(messages: list[str]) -> tuple[bool, str]:
        ws_broadcast({"snapshot": {"reading": [["buyer", m] for m in messages], "proposed": "", "running": True}})
        should, reply = decide_reply(messages)
        ws_broadcast({"snapshot": {"reading": [["buyer", m] for m in messages], "proposed": reply, "running": True}})
        return should, reply

    mirror_task = asyncio.create_task(_mirror_loop())
    try:
        if run_once:
            await _bot.run_once(hook)  # uma passada
        else:
            await _bot.run_forever(hook, idle_seconds=5.0)
    except Exception as e:
        LAST_ERR = f"{type(e).__name__}: {e}"
        log(f"[ERROR] {type(e).__name__}: {e}")
    finally:
        try:
            mirror_task.cancel()
        except Exception:
            pass
        RUNNING = False
        ws_broadcast({"snapshot": {"running": False}})
        _bot = None

@app.post("/start")
async def start():
    global _task, RUNNING
    if RUNNING:
        return RedirectResponse("/", status_code=303)
    if not duoke_is_connected():
        log("[UI] Duoke não conectado. Faça login na aba Configurações.")
    ws_broadcast({"snapshot": {"running": True}})
    _task = asyncio.create_task(_run_cycle(run_once=False))
    return RedirectResponse("/", status_code=303)

@app.post("/run-once")
async def run_once():
    global _task, RUNNING
    if RUNNING:
        return RedirectResponse("/", status_code=303)
    if not duoke_is_connected():
        log("[UI] Duoke não conectado. Faça login na aba Configurações.")
    ws_broadcast({"snapshot": {"running": True}})
    _task = asyncio.create_task(_run_cycle(run_once=True))
    return RedirectResponse("/", status_code=303)

@app.post("/stop")
async def stop():
    global RUNNING, _task
    RUNNING = False
    if _task and not _task.done():
        _task.cancel()
    return RedirectResponse("/", status_code=303)

@app.get("/status")
async def status():
    return {"running": RUNNING, "last_error": LAST_ERR}

# Ações manuais da UI (enviar/pular)
@app.post("/action/send")
async def action_send(req: Request):
    data = await req.json()
    txt = (data.get("text") or "").strip()
    bot = _bot
    page = getattr(bot, "current_page", None) if bot else None
    if page and bot and txt:
        try:
            await bot.send_reply(page, txt)
            ws_broadcast({"snapshot": {"last_action": "sent"}})
            log("[UI] resposta enviada manualmente.")
        except Exception as e:
            log(f"[UI] erro ao enviar: {type(e).__name__}: {e}")
            return JSONResponse({"ok": False, "error": str(e)})
    return JSONResponse({"ok": True})

@app.post("/action/skip")
async def action_skip():
    ws_broadcast({"snapshot": {"last_action": "skipped"}})
    log("[UI] conversa pulada manualmente.")
    return JSONResponse({"ok": True})


@app.post("/action/close-modal")
async def action_close_modal():
    bot = _bot
    page = getattr(bot, "current_page", None) if bot else None
    if page and bot:
        try:
            ok = await bot.close_modal(page)
            if ok:
                log("[UI] modal fechado manualmente.")
            else:
                log("[UI] nenhum modal visível.")
        except Exception as e:
            log(f"[UI] erro ao fechar modal: {type(e).__name__}: {e}")
            return JSONResponse({"ok": False, "error": str(e)})
    return JSONResponse({"ok": True})


@app.post("/action/mouse-click")
async def action_mouse_click(req: Request):
    bot = _bot
    page = getattr(bot, "current_page", None) if bot else None
    if not (page and bot):
        return JSONResponse({"ok": False, "error": "no active page"})
    data = await req.json()
    try:
        x = float(data.get("x", 0))
        y = float(data.get("y", 0))
        await page.mouse.move(x, y)
        await page.mouse.click(x, y)
        log(f"[UI] mouse click at {x:.0f},{y:.0f}")
    except Exception as e:
        log(f"[UI] erro mouse click: {type(e).__name__}: {e}")
        return JSONResponse({"ok": False, "error": str(e)})
    return JSONResponse({"ok": True})


@app.post("/action/submit-code")
async def action_submit_code(req: Request):
    data = await req.json()
    code = (data.get("code") or "").strip()
    bot = _bot
    page = getattr(bot, "current_page", None) if bot else None
    if page and bot and code:
        try:
            await bot.enter_verification_code(page, code)
            ws_broadcast({"snapshot": {"last_action": "code_submitted"}})
            log("[UI] código de verificação enviado.")
        except Exception as e:
            log(f"[UI] erro ao enviar código: {type(e).__name__}: {e}")
            return JSONResponse({"ok": False, "error": str(e)})
    elif not code:
        return JSONResponse({"ok": False, "error": "Código vazio."})
    return JSONResponse({"ok": True})
