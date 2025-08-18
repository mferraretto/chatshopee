# app_ui.py
# --- Força event loop correto no Windows (necessário para subprocess do Playwright) ---
import sys, asyncio
if sys.platform.startswith("win"):
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except Exception:
        pass
# ----------------------------------------------------------------------

import os, json, base64, uuid, time
from pathlib import Path
from typing import Optional, Set, Dict
from collections import deque

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Template
from playwright.async_api import async_playwright, TimeoutError as PWTimeoutError
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from src.duoke import DuokeBot
from src.config import settings
from src.classifier import decide_reply
from src.rules import load_rules, save_rules

# ===== Configuração para o login do Duoke =====
# Diretório para salvar sessões criptografadas
SESS_DIR = Path("sessions")
SESS_DIR.mkdir(exist_ok=True)
# A chave secreta deve ser definida na variável de ambiente do Render para produção
SECRET = os.getenv("SESSION_ENC_SECRET", "troque-isto-no-render").encode("utf-8")
LOGIN_WAIT_TIMEOUT = 180000  # ms (3 min) para esperar dashboard após verificar código

# Mapeamento temporário para tentativas de login
PENDING: Dict[str, Dict] = {}

# ===== Funções de criptografia (movidas de main.py) =====
def _derive_key(secret: bytes, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=100_000)
    return kdf.derive(secret)

def encrypt_bytes(data: bytes, secret: bytes) -> bytes:
    salt = os.urandom(16)
    key = _derive_key(secret, salt)
    aes = AESGCM(key)
    iv = os.urandom(12)
    ct = aes.encrypt(iv, data, None)
    return salt + iv + ct

def decrypt_bytes(encrypted_data: bytes, secret: bytes) -> bytes:
    salt = encrypted_data[:16]
    iv = encrypted_data[16:28]
    ct = encrypted_data[28:]
    key = _derive_key(secret, salt)
    aes = AESGCM(key)
    return aes.decrypt(iv, ct, None)

def session_path(user_id: str) -> Path:
    """Retorna o caminho do arquivo de sessão para um dado user_id."""
    return SESS_DIR / f"{user_id}.session"

# ===== Estado global simples =====
RUNNING: bool = False
LAST_ERR: Optional[str] = None
LOGS = deque(maxlen=4000)
_task: Optional[asyncio.Task] = None
_bot: Optional[DuokeBot] = None

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
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <script src="https://cdn.tailwindcss.com"></script>
  <style>
    body { font-family: sans-serif; }
    .tab-content { display: none; }
    .tab-content.active { display: block; }
    .tab-button.active { @apply border-b-2 border-blue-500 text-blue-500 font-medium; }
  </style>
</head>
<body class="bg-gray-100 min-h-screen">
  <div class="container mx-auto p-4 max-w-4xl">
    <div class="bg-white rounded-lg shadow-xl overflow-hidden">
      <div class="p-4 border-b">
        <nav class="flex space-x-4">
          <button class="tab-button active py-2 px-4 transition-colors duration-200" data-tab="dashboard">Dashboard</button>
          <button class="tab-button py-2 px-4 transition-colors duration-200" data-tab="logs">Logs</button>
          <button class="tab-button py-2 px-4 transition-colors duration-200" data-tab="settings">Configurações</button>
          <button class="tab-button py-2 px-4 transition-colors duration-200" data-tab="rules">Regras</button>
        </nav>
      </div>

      <!-- Dashboard -->
      <div id="dashboard" class="tab-content active p-6">
        <h1 class="text-2xl font-bold text-gray-800 mb-4">Dashboard</h1>
        <div id="status-display" class="mb-4">
          <div id="status-connected" class="hidden">
            <p class="text-sm text-gray-600">Status: <span id="is-running" class="font-bold text-red-500">Parado</span></p>
            <p class="text-sm text-gray-600">Último erro: <span id="last-error" class="font-bold text-green-500">Nenhum</span></p>
          </div>
          <div id="status-disconnected" class="flex items-center space-x-2">
            <svg class="w-5 h-5 text-red-500" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clip-rule="evenodd"></path></svg>
            <span class="text-sm text-gray-600 font-bold">Duoke não conectado.</span>
          </div>
        </div>

        <div id="running-controls" class="space-y-4 hidden">
          <button id="start-button" class="w-full bg-green-500 hover:bg-green-600 text-white font-bold py-2 px-4 rounded-lg shadow-md transition-colors duration-200">
            <svg class="animate-spin inline mr-2 w-4 h-4 text-white hidden" id="spinner" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>
            Iniciar Monitoramento
          </button>
          <button id="stop-button" class="w-full bg-red-500 hover:bg-red-600 text-white font-bold py-2 px-4 rounded-lg shadow-md transition-colors duration-200">
            Parar Monitoramento
          </button>
        </div>

        <div id="manual-action" class="mt-8 space-y-4 p-4 bg-gray-50 rounded-lg hidden">
          <h2 class="text-xl font-bold text-gray-800">Ação Manual</h2>
          <div class="flex space-x-2">
            <input type="text" id="manual-text" placeholder="Digite uma resposta..." class="flex-grow p-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500">
            <button id="send-button" class="bg-blue-500 hover:bg-blue-600 text-white font-bold py-2 px-4 rounded-lg shadow-md transition-colors duration-200">Enviar</button>
            <button id="skip-button" class="bg-yellow-500 hover:bg-yellow-600 text-white font-bold py-2 px-4 rounded-lg shadow-md transition-colors duration-200">Pular</button>
          </div>
        </div>
      </div>

      <!-- Logs -->
      <div id="logs" class="tab-content p-6">
        <h1 class="text-2xl font-bold text-gray-800 mb-4">Logs</h1>
        <div id="log-container" class="bg-gray-800 text-gray-200 p-4 rounded-lg shadow-inner overflow-y-auto max-h-96 text-xs whitespace-pre-wrap font-mono"></div>
      </div>

      <!-- Configurações -->
      <div id="settings" class="tab-content p-6">
        <h1 class="text-2xl font-bold text-gray-800 mb-4">Configurações</h1>
        <form id="login-form" class="space-y-4">
          <div class="flex items-center">
            <label for="email" class="w-32 font-medium text-gray-700">Email:</label>
            <input type="email" id="email" name="email" required class="flex-grow p-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500">
          </div>
          <div class="flex items-center">
            <label for="password" class="w-32 font-medium text-gray-700">Senha:</label>
            <input type="password" id="password" name="password" required class="flex-grow p-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500">
          </div>
          <div class="flex items-center">
            <label for="phone" class="w-32 font-medium text-gray-700">Celular:</label>
            <input type="tel" id="phone" name="phone" placeholder="DDD+Número (opcional)" class="flex-grow p-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500">
          </div>
          <button type="submit" class="w-full bg-blue-500 hover:bg-blue-600 text-white font-bold py-2 px-4 rounded-lg shadow-md transition-colors duration-200">Fazer Login</button>
        </form>

        <form id="otp-form" class="mt-8 space-y-4 hidden">
          <div class="flex items-center">
            <label for="otp" class="w-32 font-medium text-gray-700">Código OTP:</label>
            <input type="text" id="otp" name="otp" required class="flex-grow p-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-purple-500">
          </div>
          <button type="submit" class="w-full bg-purple-500 hover:bg-purple-600 text-white font-bold py-2 px-4 rounded-lg shadow-md transition-colors duration-200">Verificar Código</button>
        </form>

        <button id="logout-button" class="mt-4 w-full bg-red-500 hover:bg-red-600 text-white font-bold py-2 px-4 rounded-lg shadow-md transition-colors duration-200 hidden">Desconectar</button>
      </div>
      
      <!-- Regras -->
      <div id="rules" class="tab-content p-6">
        <h1 class="text-2xl font-bold text-gray-800 mb-4">Regras</h1>
        <div class="mb-4">
          <p class="text-sm text-gray-600">Edite as regras do bot aqui. As alterações são salvas automaticamente.</p>
        </div>
        <div id="rules-editor" class="bg-gray-800 text-gray-200 p-4 rounded-lg shadow-inner overflow-y-auto max-h-96 text-xs whitespace-pre-wrap font-mono" contenteditable="true"></div>
      </div>

    </div>
  </div>

  <script>
    const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
    const websocket = new WebSocket(`${protocol}://${window.location.host}/ws`);
    const tabs = document.querySelectorAll('.tab-button');
    const tabContents = document.querySelectorAll('.tab-content');
    const logContainer = document.getElementById('log-container');
    const startButton = document.getElementById('start-button');
    const stopButton = document.getElementById('stop-button');
    const sendButton = document.getElementById('send-button');
    const skipButton = document.getElementById('skip-button');
    const manualText = document.getElementById('manual-text');
    const rulesEditor = document.getElementById('rules-editor');
    const statusConnected = document.getElementById('status-connected');
    const statusDisconnected = document.getElementById('status-disconnected');
    const runningControls = document.getElementById('running-controls');
    const manualAction = document.getElementById('manual-action');
    const loginForm = document.getElementById('login-form');
    const otpForm = document.getElementById('otp-form');
    const logoutButton = document.getElementById('logout-button');
    const isRunning = document.getElementById('is-running');
    const lastError = document.getElementById('last-error');
    const spinner = document.getElementById('spinner');

    let currentStatus = {};
    let saveTimeout;

    // ----- UI Actions -----
    tabs.forEach(tab => {
      tab.addEventListener('click', () => {
        tabs.forEach(t => t.classList.remove('active'));
        tab.classList.add('active');
        tabContents.forEach(content => content.classList.remove('active'));
        document.getElementById(tab.dataset.tab).classList.add('active');
        if (tab.dataset.tab === 'rules') {
            loadRules();
        }
      });
    });

    startButton.addEventListener('click', async () => {
      startButton.disabled = true;
      spinner.classList.remove('hidden');
      try {
        await fetch('/start', { method: 'POST' });
      } finally {
        startButton.disabled = false;
        spinner.classList.add('hidden');
      }
    });

    stopButton.addEventListener('click', async () => {
      await fetch('/stop', { method: 'POST' });
    });

    sendButton.addEventListener('click', async () => {
      const text = manualText.value;
      if (text) {
        await fetch('/action/send', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text })
        });
        manualText.value = '';
      }
    });

    skipButton.addEventListener('click', async () => {
      await fetch('/action/skip', { method: 'POST' });
    });

    loginForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const data = new FormData(e.target);
      const email = data.get('email');
      const password = data.get('password');
      const phone = data.get('phone');
      const payload = { email, password, phone };

      try {
        const res = await fetch('/duoke/login', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        const result = await res.json();
        if (result.ok) {
          showCustomAlert('Login iniciado. Verifique seu email ou celular por um código.');
          loginForm.classList.add('hidden');
          otpForm.classList.remove('hidden');
        } else {
          showCustomAlert(`Erro: ${result.msg}`);
        }
      } catch (err) {
        showCustomAlert('Ocorreu um erro no login. Verifique o email/senha ou tente novamente mais tarde.');
      }
    });

    otpForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const otp = document.getElementById('otp').value;
      const attempt_id = "default_attempt"; // Placeholder for demo
      try {
        const res = await fetch('/duoke/verify', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ code: otp, attempt_id })
        });
        const result = await res.json();
        if (result.ok) {
          showCustomAlert('Login bem-sucedido!');
          otpForm.classList.add('hidden');
          loginForm.classList.add('hidden');
          logoutButton.classList.remove('hidden');
          checkStatus();
        } else {
          showCustomAlert(`Erro: ${result.msg}`);
        }
      } catch (err) {
        showCustomAlert('Ocorreu um erro ao verificar o código. Tente novamente.');
      }
    });

    logoutButton.addEventListener('click', async () => {
      await fetch('/duoke/logout', { method: 'POST' });
      loginForm.classList.remove('hidden');
      logoutButton.classList.add('hidden');
      checkStatus();
    });
    
    // Auto-save rules with debounce
    rulesEditor.addEventListener('input', () => {
      clearTimeout(saveTimeout);
      saveTimeout = setTimeout(saveRules, 1000);
    });

    // ----- API Calls -----
    async function checkStatus() {
        const res = await fetch('/status');
        const data = await res.json();
        updateUI(data);
    }
    
    async function loadRules() {
        try {
            const res = await fetch('/rules');
            const rules = await res.json();
            rulesEditor.textContent = JSON.stringify(rules, null, 2);
        } catch (e) {
            console.error('Failed to load rules:', e);
            rulesEditor.textContent = 'Failed to load rules.';
        }
    }
    
    async function saveRules() {
        try {
            const rules = JSON.parse(rulesEditor.textContent);
            const res = await fetch('/rules', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(rules)
            });
            if (res.ok) {
                console.log('Rules saved successfully.');
            } else {
                console.error('Failed to save rules:', await res.text());
            }
        } catch (e) {
            console.error('Invalid JSON format:', e);
        }
    }

    // ----- WebSocket -----
    websocket.onmessage = (event) => {
      const data = JSON.parse(event.data);
      if (data.snapshot) {
        updateUI(data.snapshot);
      }
      if (data.log) {
        logContainer.innerHTML = data.log.map(l => '<div>' + l + '</div>').join('') + logContainer.innerHTML;
        if (logContainer.children.length > 4000) {
          while(logContainer.children.length > 3900) {
            logContainer.removeChild(logContainer.lastChild);
          }
        }
      }
    };
    
    websocket.onopen = (event) => {
      console.log("WebSocket connection established.");
      checkStatus();
      loadRules();
    };

    websocket.onclose = (event) => {
      console.log("WebSocket connection closed.");
    };

    websocket.onerror = (error) => {
      console.error("WebSocket error:", error);
    };

    // ----- UI Update Logic -----
    function updateUI(status) {
      if (status.running !== undefined) {
        currentStatus.running = status.running;
        if (currentStatus.running) {
          isRunning.textContent = 'Rodando';
          isRunning.classList.remove('text-red-500');
          isRunning.classList.add('text-green-500');
          startButton.classList.add('hidden');
          stopButton.classList.remove('hidden');
        } else {
          isRunning.textContent = 'Parado';
          isRunning.classList.remove('text-green-500');
          isRunning.classList.add('text-red-500');
          startButton.classList.remove('hidden');
          stopButton.classList.add('hidden');
        }
      }

      if (status.last_error !== undefined) {
        currentStatus.last_error = status.last_error;
        lastError.textContent = currentStatus.last_error || 'Nenhum';
        lastError.classList.remove('text-green-500', 'text-red-500');
        if (currentStatus.last_error) {
            lastError.classList.add('text-red-500');
        } else {
            lastError.classList.add('text-green-500');
        }
      }

      if (status.is_duoke_connected !== undefined) {
        if (status.is_duoke_connected) {
          statusConnected.classList.remove('hidden');
          statusDisconnected.classList.add('hidden');
          runningControls.classList.remove('hidden');
          manualAction.classList.remove('hidden');
          loginForm.classList.add('hidden');
          logoutButton.classList.remove('hidden');
        } else {
          statusConnected.classList.add('hidden');
          statusDisconnected.classList.remove('hidden');
          runningControls.classList.add('hidden');
          manualAction.classList.add('hidden');
          loginForm.classList.remove('hidden');
          logoutButton.classList.add('hidden');
        }
      }
    }

    // Modal para substituir alerts
    function showCustomAlert(message) {
      const modal = document.createElement('div');
      modal.className = 'fixed inset-0 bg-gray-600 bg-opacity-50 overflow-y-auto h-full w-full flex items-center justify-center z-50';
      modal.innerHTML = `
        <div class="p-6 bg-white rounded-lg shadow-xl max-w-sm mx-auto">
          <p class="text-lg font-bold text-gray-800 mb-4">${message}</p>
          <button id="close-modal-btn" class="w-full bg-blue-500 hover:bg-blue-600 text-white font-bold py-2 px-4 rounded-lg shadow-md transition-colors duration-200">OK</button>
        </div>
      `;
      document.body.appendChild(modal);
      document.getElementById('close-modal-btn').addEventListener('click', () => {
        document.body.removeChild(modal);
      });
    }
  </script>
</body>
</html>
""")

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")

CONNECTIONS: Set[WebSocket] = set()

async def ws_broadcast(msg: Dict):
    if "log" in msg:
        msg["log"] = list(LOGS) # snapshot do log
    for conn in list(CONNECTIONS):
        try:
            await conn.send_json(msg)
        except WebSocketDisconnect:
            CONNECTIONS.remove(conn)
        except RuntimeError:
            CONNECTIONS.remove(conn)

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    CONNECTIONS.add(websocket)
    try:
        # Envia estado inicial e logs
        await websocket.send_json({"snapshot": {"running": RUNNING, "last_error": LAST_ERR, "is_duoke_connected": duoke_is_connected()}, "log": list(LOGS)})
        while True:
            await websocket.receive_text() # Espera por mensagens do cliente para manter a conexão aberta
    except WebSocketDisconnect:
        CONNECTIONS.remove(websocket)

@app.get("/", response_class=HTMLResponse)
async def get_ui():
    return HTMLResponse(HTML.render())

# Adiciona um endpoint de health check para a plataforma Render
@app.get("/healthz")
async def healthz():
    return JSONResponse({"status": "ok"})

# ===== Endpoints de Login (movidos de main.py e atualizados para JSON) =====
@app.post("/duoke/login")
async def duoke_login(req: Request, user_id: str = "default_user"):
    global PENDING
    data = await req.json()
    email = data.get("email")
    password = data.get("password")
    phone = data.get("phone")

    if not email or not password:
        raise HTTPException(400, "Email e senha são obrigatórios.")

    attempt_id = str(uuid.uuid4())
    PENDING[attempt_id] = {"ts": time.time(), "user_id": user_id}

    async def _do_login():
        nonlocal attempt_id
        async with async_playwright() as p:
            browser = None
            try:
                browser = await p.chromium.launch(headless=True)
                ctx = await browser.new_context()
                page = await ctx.new_page()

                log(f"[Playwright] Navegando para a página de login para o usuário {user_id}...")
                await page.goto("https://web.duoke.com/?lang=en#/dk/login")
                
                await page.fill("input[name='email']", email)
                await page.fill("input[name='password']", password)
                
                if phone:
                    await page.fill("input[placeholder='Telefone']", phone)
                    await page.get_by_role("button", name="Send SMS").click()
                else:
                    await page.get_by_role("button", name="Login").click()

                await page.wait_for_url(lambda url: "verify" in url or "dashboard" in url, timeout=LOGIN_WAIT_TIMEOUT)

                if "dashboard" in page.url:
                    log(f"[Playwright] Login bem-sucedido para {user_id}. Salvando sessão.")
                    await ctx.storage_state(path=session_path(user_id))
                    PENDING.pop(attempt_id, None)
                    return JSONResponse({"ok": True, "status": "LOGGED", "msg": "Sessão criada com sucesso."})

                elif "verify" in page.url:
                    log(f"[Playwright] OTP necessário para {user_id}. Aguardando verificação.")
                    PENDING[attempt_id]["ctx"] = ctx
                    return JSONResponse({"ok": True, "status": "OTP_REQUIRED", "attempt_id": attempt_id, "msg": "Código OTP necessário."})
                else:
                    raise Exception("Falha desconhecida no login.")
            except Exception as e:
                if browser and browser.is_connected():
                    await browser.close()
                PENDING.pop(attempt_id, None)
                raise HTTPException(400, f"Falha no login: {e}")

    return await _do_login()

@app.post("/duoke/verify")
async def duoke_verify(req: Request):
    global PENDING
    data = await req.json()
    code = data.get("code")
    attempt_id = data.get("attempt_id")

    if not code or not attempt_id or attempt_id not in PENDING:
        raise HTTPException(400, "Código inválido ou tentativa de login expirada.")
    
    pending_data = PENDING[attempt_id]
    ctx = pending_data.get("ctx")
    user_id = pending_data.get("user_id")

    if not ctx:
        raise HTTPException(400, "Contexto de verificação não encontrado.")
    
    try:
        page = await ctx.new_page()

        sel_code = "input[placeholder*='verification' i], input[type='tel']"
        await page.fill(sel_code, code)
        
        try:
            await page.get_by_role("button", name=lambda n: n and ('verify' in n.lower() or 'confirm' in n.lower() or 'submit' in n.lower() or 'login' in n.lower())).click(timeout=2000)
        except PWTimeoutError:
            await page.locator("button").first.click()

        await page.wait_for_load_state("networkidle", timeout=LOGIN_WAIT_TIMEOUT)
        
        tmp = Path("storage_state.json")
        await ctx.storage_state(path=str(tmp))
        enc = encrypt_bytes(tmp.read_bytes(), SECRET)
        session_path(user_id).write_bytes(enc)
        tmp.unlink(missing_ok=True)
        
        await ctx.close()
        PENDING.pop(attempt_id, None)
        return JSONResponse({"ok": True, "status": "LOGGED", "msg": "Sessão criada com sucesso."})

    except Exception as e:
        try:
            await ctx.close()
        finally:
            PENDING.pop(attempt_id, None)
        raise HTTPException(400, f"Falha ao verificar código: {e}")


@app.post("/start")
async def start():
    global _task, _bot, RUNNING, LAST_ERR
    if RUNNING:
        return RedirectResponse("/", status_code=303)
    if not duoke_is_connected():
        log("[UI] Duoke não conectado. Faça login na aba Configurações.")
        return RedirectResponse("/", status_code=303)
    ws_broadcast({"snapshot": {"running": True}})
    
    try:
        _bot = DuokeBot(STATE_PATH)
        await _bot.start()
        log("[UI] Bot iniciado com sucesso.")
        LAST_ERR = None
    except Exception as e:
        log(f"[UI] Erro ao iniciar bot: {e}")
        LAST_ERR = str(e)
        RUNNING = False
        if _bot:
            await _bot.close()
        _bot = None
        ws_broadcast({"snapshot": {"running": False, "last_error": LAST_ERR}})
        return RedirectResponse("/", status_code=303)
    
    RUNNING = True
    _task = asyncio.create_task(_run_cycle())
    
    return RedirectResponse("/", status_code=303)

async def _run_cycle():
    global RUNNING, LAST_ERR, _bot
    
    log("[BOT] Ciclo de monitoramento iniciado.")
    try:
        while RUNNING:
            if not _bot:
                log("[BOT] Instância do bot não encontrada. Parando.")
                RUNNING = False
                break
            
            try:
                new_requests = await _bot.check_new_messages()
                if new_requests:
                    log(f"[BOT] Encontrado {len(new_requests)} novas solicitações.")
                    for req in new_requests:
                        reply = decide_reply(req.text)
                        
                        if reply["action"] == "reply":
                            log(f"[BOT] Respondendo com regra '{reply['id']}'.")
                            await _bot.send_reply(req.page, reply["text"])
                        elif reply["action"] == "skip":
                            log(f"[BOT] Pulando solicitação '{reply['id']}'.")
                            await _bot.skip_ticket(req.page)
                            
                        await asyncio.sleep(1)
            except Exception as e:
                log(f"[BOT] Erro durante o ciclo: {type(e).__name__}: {e}")
                LAST_ERR = str(e)
                if not isinstance(e, asyncio.CancelledError):
                    log("[BOT] Tentando reiniciar o bot...")
                    if _bot:
                        await _bot.close()
                    try:
                        _bot = DuokeBot(STATE_PATH)
                        await _bot.start()
                    except Exception as restart_e:
                        log(f"[BOT] Falha ao reiniciar o bot: {restart_e}. Parando.")
                        RUNNING = False
                        break
            
            await asyncio.sleep(5)
            
    except asyncio.CancelledError:
        log("[BOT] Ciclo de monitoramento cancelado.")
    finally:
        RUNNING = False
        if _bot:
            await _bot.close()
            _bot = None
        log("[BOT] Ciclo de monitoramento finalizado.")
        ws_broadcast({"snapshot": {"running": False}})

@app.post("/stop")
async def stop():
    global RUNNING, _task
    RUNNING = False
    if _task and not _task.done():
        _task.cancel()
    return RedirectResponse("/", status_code=303)

@app.get("/status")
async def status():
    return {"running": RUNNING, "last_error": LAST_ERR, "is_duoke_connected": duoke_is_connected()}

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
            return JSONResponse({"ok": True})
        except Exception as e:
            log(f"[UI] erro ao enviar: {type(e).__name__}: {e}")
            return JSONResponse({"ok": False, "error": str(e)})

@app.post("/action/skip")
async def action_skip():
    bot = _bot
    page = getattr(bot, "current_page", None) if bot else None
    if page and bot:
        try:
            await bot.skip_ticket(page)
            ws_broadcast({"snapshot": {"last_action": "skipped"}})
            log("[UI] solicitação pulada manualmente.")
            return JSONResponse({"ok": True})
        except Exception as e:
            log(f"[UI] erro ao pular: {type(e).__name__}: {e}")
            return JSONResponse({"ok": False, "error": str(e)})

@app.get("/rules")
async def get_rules():
    try:
        rules = load_rules()
        return JSONResponse(rules)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/rules")
async def post_rules(req: Request):
    try:
        rules = await req.json()
        save_rules(rules)
        log("[RULES] Regras salvas com sucesso.")
        return JSONResponse({"ok": True})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao salvar regras: {e}")
