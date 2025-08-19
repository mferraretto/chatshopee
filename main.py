import os
import json
import base64
import uuid
import asyncio
from pathlib import Path
from typing import Dict, Optional

# Importações do FastAPI para criar o servidor web e definir as rotas
from fastapi import FastAPI, HTTPException, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

# Importações do Playwright para automatizar o navegador
from playwright.async_api import async_playwright, TimeoutError as PWTimeoutError

# Configurações compartilhadas
from src.config import settings

# Importações de Criptografia para garantir a segurança da sessão
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# ===== Configurações da Aplicação =====
SESS_DIR = Path("sessions") # Diretório para armazenar os arquivos de sessão
SESS_DIR.mkdir(exist_ok=True)
SECRET = os.getenv("SESSION_ENC_SECRET", "troque-isto-no-render") # Chave secreta para criptografia
LOGIN_WAIT_TIMEOUT = 180000 # Tempo máximo de espera para o login (em ms)

# ===== Funções de Criptografia para salvar a sessão do Playwright de forma segura =====
def _derive_key(secret: str, salt: bytes) -> bytes:
    """Deriva uma chave segura a partir de uma senha e um sal usando PBKDF2."""
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=100_000)
    return kdf.derive(secret.encode("utf-8"))

def encrypt_bytes(data: bytes, secret: str) -> bytes:
    """Criptografa dados usando AES-GCM. Retorna o sal, IV e o texto cifrado."""
    salt = os.urandom(16)
    key = _derive_key(secret, salt)
    aes = AESGCM(key)
    iv = os.urandom(12)
    ct = aes.encrypt(iv, data, None)
    return salt + iv + ct

def decrypt_bytes(packed: bytes, secret: str) -> bytes:
    """Descriptografa dados empacotados pelo `encrypt_bytes`."""
    salt, iv, ct = packed[:16], packed[16:28], packed[28:]
    key = _derive_key(secret, salt)
    aes = AESGCM(key)
    return aes.decrypt(iv, ct, None)

def session_path(user_id: str) -> Path:
    """Gera o caminho do arquivo de sessão para um dado user_id."""
    return SESS_DIR / f"{user_id}.bin"

# ===== Estado de login pendente (em memória, com TTL) =====
# Isso é usado para manter o estado entre as duas requisições (iniciar e enviar código)
class Pending:
    """Classe para armazenar o estado de uma tentativa de login pendente."""
    def __init__(self, browser, context, page, user_id):
        self.browser = browser
        self.context = context
        self.page = page
        self.user_id = user_id
        self.created = asyncio.get_event_loop().time()

PENDING: Dict[str, Pending] = {} # Dicionário para armazenar as tentativas pendentes
PENDING_TTL = 10 * 60  # Tempo de vida da tentativa pendente: 10 minutos

async def cleanup_pending():
    """Remove tentativas de login pendentes que expiraram."""
    now = asyncio.get_event_loop().time()
    stale = [k for k, v in PENDING.items() if now - v.created > PENDING_TTL]
    for k in stale:
        try:
            await PENDING[k].browser.close()
        except Exception:
            pass
        PENDING.pop(k, None)

# ===== Definição da API FastAPI =====
app = FastAPI()

# Rota de health check. Usada por plataformas de deploy (como o Render) para verificar se a app está rodando.
@app.get("/healthz")
async def health_check():
    return {"status": "ok"}

# Rota principal que serve a página HTML de login.
@app.get("/", response_class=HTMLResponse)
def home():
    return """
    <html><body style="font-family: Inter, Arial; color:#eee; background:#0f1115; padding:24px;">
      <h2>Login Duoke (2 etapas)</h2>
      <form id="f1" method="post" action="/duoke/login/start" onsubmit="event.preventDefault(); startLogin();">
        <label>Seu UID: <input id="uid" name="user_id" required></label><br/><br/>
        <label>Email Duoke: <input id="email" name="email" type="email" required></label><br/><br/>
        <label>Senha Duoke: <input id="password" name="password" type="password" required></label><br/><br/>
        <label>Verificação (captcha): <input id="captcha" name="captcha"></label><br/><br/>
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
          fd.append('captcha', document.getElementById('captcha').value);
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

# Rota para iniciar o processo de login
@app.post("/duoke/login/start")
async def duoke_login_start(
    user_id: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    captcha: Optional[str] = Form(None),
):
    await cleanup_pending() # Limpa tentativas expiradas antes de começar
    p = None
    browser = None
    try:
        # Inicia o Playwright
        p = await async_playwright().start()
        # Lança o navegador Chromium em modo headless
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"]
        )
        ctx = await browser.new_context()
        page = await ctx.new_page()
        # Navega para a página de login
        await page.goto(
            "https://www.duoke.com/",
            wait_until="domcontentloaded",
            timeout=settings.goto_timeout_ms,
        )

        # Tenta fechar o modal "Your login has expired..." ou similar, se aparecer
        try:
            await page.get_by_role(
                "button",
                name=lambda n: n and ("confirm" in n.lower() or "ok" in n.lower()),
            ).click(timeout=10000)
        except PWTimeoutError:
            pass

        # Preenche o formulário de login com as informações fornecidas
        await page.fill("input[type='email'], input[placeholder='Email']", email)
        await page.fill("input[type='password'], input[placeholder='Password']", password)
        if captcha:
            try:
                await page.fill(
                    "input[name*='captcha' i], input[placeholder*='captcha' i], input[name*='verify' i], input[placeholder*='verification' i]",
                    captcha,
                )
            except PWTimeoutError:
                pass
        await page.get_by_role("button", name="Login").click()

        # Tenta detectar o dashboard imediatamente (se o 2FA não for necessário)
        try:
            await page.wait_for_load_state("networkidle", timeout=4000)
            tmp = Path("storage_state.json")
            await ctx.storage_state(path=str(tmp))
            enc = encrypt_bytes(tmp.read_bytes(), SECRET)
            session_path(user_id).write_bytes(enc)
            tmp.unlink(missing_ok=True)
            return JSONResponse({"ok": True, "status": "LOGGED", "msg": "Sessão criada sem pedir código."})
        except Exception:
            pass

        # Se não foi para o dashboard, tenta detectar a UI de verificação de código
        code_input = page.locator("input[name*='code' i], input[placeholder*='code' i], input[placeholder*='verification' i], input[type='tel']")
        if await code_input.count() == 0:
            try:
                await code_input.wait_for(timeout=8000)
            except Exception:
                # Se não encontrar o campo de código, assume que o login falhou por outro motivo
                raise HTTPException(400, "Não foi possível localizar o campo de código. Verifique o login.")

        # Cria uma tentativa pendente para o segundo passo (código)
        attempt_id = uuid.uuid4().hex
        PENDING[attempt_id] = Pending(browser, ctx, page, user_id)
        return JSONResponse({"ok": True, "status": "NEED_CODE", "attempt_id": attempt_id, "msg": "Código de verificação necessário."})

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"Falha ao iniciar login: {e}")
    finally:
        if p and not browser:
            await p.stop()

# Rota para enviar o código de verificação
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
        # Preenche o campo do código de verificação
        sel_code = "input[name*='code' i], input[placeholder*='code' i], input[placeholder*='verification' i], input[type='tel']"
        await page.fill(sel_code, code)

        # Clica no botão para confirmar/verificar
        try:
            await page.get_by_role("button", name=lambda n: n and ('verify' in n.lower() or 'confirm' in n.lower() or 'submit' in n.lower() or 'login' in n.lower())).click(timeout=2000)
        except PWTimeoutError:
            await page.locator("button").first.click()

        # Espera o dashboard carregar e salva a sessão
        await page.wait_for_load_state("networkidle", timeout=LOGIN_WAIT_TIMEOUT)
        tmp = Path("storage_state.json")
        await ctx.storage_state(path=str(tmp))
        enc = encrypt_bytes(tmp.read_bytes(), SECRET)
        session_path(user_id).write_bytes(enc)
        tmp.unlink(missing_ok=True)

        # Encerra o navegador e remove a tentativa pendente
        await browser.close()
        PENDING.pop(attempt_id, None)
        return JSONResponse({"ok": True, "status": "LOGGED", "msg": "Sessão criada com sucesso."})

    except Exception as e:
        try:
            await browser.close()
        finally:
            PENDING.pop(attempt_id, None)
        raise HTTPException(400, f"Falha ao verificar código: {e}")

# Rota para verificar o status da sessão de um usuário
@app.get("/duoke/status")
def duoke_status(user_id: str):
    return {"logged": session_path(user_id).exists()}

# Rota para fazer logout
@app.post("/duoke/logout")
def duoke_logout(user_id: str):
    p = session_path(user_id)
    if p.exists():
        p.unlink()
    return {"ok": True}
