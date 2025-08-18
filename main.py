import os, json, base64, uuid, asyncio
from pathlib import Path
from typing import Dict, Optional
from fastapi import FastAPI, HTTPException, Form
from fastapi.responses import HTMLResponse, JSONResponse
from playwright.async_api import async_playwright, TimeoutError as PWTimeoutError
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# ===== Config =====
SESS_DIR = Path("sessions"); SESS_DIR.mkdir(exist_ok=True)
SECRET = os.getenv("SESSION_ENC_SECRET", "troque-isto-no-render")  # defina no Render
LOGIN_WAIT_TIMEOUT = 180000  # ms (3 min) para esperar dashboard após verificar código

# ===== Cripto AES-GCM com PBKDF2 (igual ao seu padrão) =====
def _derive_key(secret: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=100_000)
    return kdf.derive(secret.encode("utf-8"))

def encrypt_bytes(data: bytes, secret: str) -> bytes:
    salt = os.urandom(16); key = _derive_key(secret, salt); aes = AESGCM(key); iv = os.urandom(12)
    ct = aes.encrypt(iv, data, None)
    return salt + iv + ct  # pacote

def decrypt_bytes(packed: bytes, secret: str) -> bytes:
    salt, iv, ct = packed[:16], packed[16:28], packed[28:]
    key = _derive_key(secret, salt); aes = AESGCM(key)
    return aes.decrypt(iv, ct, None)

def session_path(user_id: str) -> Path:
    return SESS_DIR / f"{user_id}.bin"

# ===== Estado de login pendente (em memória, com TTL) =====
class Pending:
    def __init__(self, browser, context, page, user_id):
        self.browser = browser
        self.context = context
        self.page = page
        self.user_id = user_id
        self.created = asyncio.get_event_loop().time()

PENDING: Dict[str, Pending] = {}
PENDING_TTL = 10 * 60  # 10 min

async def cleanup_pending():
    # simples coletor (chamado no fim de cada request relevante)
    now = asyncio.get_event_loop().time()
    stale = [k for k,v in PENDING.items() if now - v.created > PENDING_TTL]
    for k in stale:
        try:
            await PENDING[k].browser.close()
        except Exception:
            pass
        PENDING.pop(k, None)

# ===== FastAPI =====
app = FastAPI()

@app.get("/", response_class=HTMLResponse)
def home():
    return """
    <html><body style="font-family: Inter, Arial; color:#eee; background:#0f1115; padding:24px;">
      <h2>Login Duoke (2 etapas)</h2>
      <form id="f1" method="post" action="/duoke/login/start" onsubmit="event.preventDefault(); startLogin();">
        <label>Seu UID: <input id="uid" name="user_id" required></label><br/><br/>
        <label>Email Duoke: <input id="email" name="email" type="email" required></label><br/><br/>
        <label>Senha Duoke: <input id="password" name="password" type="password" required></label><br/><br/>
        <button>Iniciar login</button>
      </form>
      <div id="step2" style="display:none; margin-top:20px;">
        <p>Insira o código de verificação enviado pelo Duoke:</p>
        <input id="code" placeholder="Código">
        <button onclick="sendCode()">Enviar código</button>
      </div>
      <pre id="out" style="margin-top:20px; background:#151821; padding:12px; border-radius:8px;"></pre>
      <script>
        let attemptId = null; let userId = null;
        async function startLogin(){
          const fd = new FormData();
          userId = document.getElementById('uid').value;
          fd.append('user_id', userId);
          fd.append('email', document.getElementById('email').value);
          fd.append('password', document.getElementById('password').value);
          const rs = await fetch('/duoke/login/start', {method:'POST', body: fd});
          const js = await rs.json();
          document.getElementById('out').textContent = JSON.stringify(js, null, 2);
          if(js.status === 'NEED_CODE'){ attemptId = js.attempt_id; document.getElementById('step2').style.display='block'; }
        }
        async function sendCode(){
          const fd = new FormData();
          fd.append('attempt_id', attemptId);
          fd.append('user_id', userId);
          fd.append('code', document.getElementById('code').value);
          const rs = await fetch('/duoke/login/code', {method:'POST', body: fd});
          const js = await rs.json();
          document.getElementById('out').textContent = JSON.stringify(js, null, 2);
        }
      </script>
    </body></html>
    """

@app.post("/duoke/login/start")
async def duoke_login_start(user_id: str = Form(...), email: str = Form(...), password: str = Form(...)):
    await cleanup_pending()
    try:
        p = await async_playwright().start()
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await page.goto("https://www.duoke.com/", wait_until="domcontentloaded")

        # Modal "Your login has expired..."
        try:
            await page.get_by_role("button", name="Confirm").click(timeout=2000)
        except PWTimeoutError:
            pass

        # Preenche login
        await page.fill("input[type='email'], input[placeholder='Email']", email)
        await page.fill("input[type='password'], input[placeholder='Password']", password)
        await page.get_by_role("button", name="Login").click()

        # 1) Tenta detectar imediatamente dashboard (não pediu código)
        try:
            await page.wait_for_load_state("networkidle", timeout=4000)
            # Se carregou algo que não é login, ótimo — salva sessão e finaliza
            tmp = Path("storage_state.json")
            await ctx.storage_state(path=str(tmp))
            enc = encrypt_bytes(tmp.read_bytes(), SECRET)
            session_path(user_id).write_bytes(enc)
            tmp.unlink(missing_ok=True)
            await browser.close(); await p.stop()
            return JSONResponse({"ok": True, "status": "LOGGED", "msg": "Sessão criada sem pedir código."})
        except Exception:
            pass

        # 2) Detecta UI de verificação de código
        # Seletor amplo: input para "code" e botões comuns
        code_input = page.locator("input[name*='code' i], input[placeholder*='code' i], input[placeholder*='verification' i], input[type='tel']")
        if await code_input.count() == 0:
            # Algumas UIs mostram um texto; como fallback, ainda vamos esperar o input surgir
            try:
                await code_input.wait_for(timeout=8000)
            except Exception:
                # Não achou input → provavelmente login falhou por outro motivo
                await browser.close(); await p.stop()
                raise HTTPException(400, "Não foi possível localizar o campo de código. Verifique o login.")

        # Cria tentativa pendente
        attempt_id = uuid.uuid4().hex
        PENDING[attempt_id] = Pending(browser, ctx, page, user_id)
        return JSONResponse({"ok": True, "status": "NEED_CODE", "attempt_id": attempt_id, "msg": "Código de verificação necessário."})

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"Falha ao iniciar login: {e}")

@app.post("/duoke/login/code")
async def duoke_login_code(attempt_id: str = Form(...), user_id: str = Form(...), code: str = Form(...)):
    await cleanup_pending()
    pend = PENDING.get(attempt_id)
    if not pend or pend.user_id != user_id:
        raise HTTPException(404, "Tentativa não encontrada/expirada.")

    page = pend.page
    ctx = pend.context
    browser = pend.browser

    try:
        # Preenche código (vários seletores tentativos)
        sel_code = "input[name*='code' i], input[placeholder*='code' i], input[placeholder*='verification' i], input[type='tel']"
        await page.fill(sel_code, code)

        # Botão para confirmar/verificar
        try:
            await page.get_by_role("button", name=lambda n: n and ('verify' in n.lower() or 'confirm' in n.lower() or 'submit' in n.lower() or 'login' in n.lower())).click(timeout=2000)
        except PWTimeoutError:
            # fallback: clique no primeiro botão
            await page.locator("button").first.click()

        # Espera o pós-login (dashboard) e salva sessão
        await page.wait_for_load_state("networkidle", timeout=LOGIN_WAIT_TIMEOUT)
        tmp = Path("storage_state.json")
        await ctx.storage_state(path=str(tmp))
        enc = encrypt_bytes(tmp.read_bytes(), SECRET)
        session_path(user_id).write_bytes(enc)
        tmp.unlink(missing_ok=True)

        # encerra e limpa pendência
        await browser.close()
        PENDING.pop(attempt_id, None)
        return JSONResponse({"ok": True, "status": "LOGGED", "msg": "Sessão criada com sucesso."})

    except Exception as e:
        try:
            await browser.close()
        finally:
            PENDING.pop(attempt_id, None)
        raise HTTPException(400, f"Falha ao verificar código: {e}")

@app.get("/duoke/status")
def duoke_status(user_id: str):
    return {"logged": session_path(user_id).exists()}

@app.post("/duoke/logout")
def duoke_logout(user_id: str):
    p = session_path(user_id)
    if p.exists(): p.unlink()
    return {"ok": True}
