import os, json, base64, uuid, asyncio
from pathlib import Path
from typing import Dict, Optional
from fastapi import FastAPI, HTTPException, Form
from fastapi.responses import HTMLResponse, JSONResponse
from playwright.async_api import async_playwright, TimeoutError as PWTimeoutError
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# ===== Configurações =====
SESS_DIR = Path("sessions")
SESS_DIR.mkdir(exist_ok=True)
SECRET = os.getenv("SESSION_ENC_SECRET", "troque-isto-no-render")  # Defina no Render
LOGIN_WAIT_TIMEOUT = 180000  # ms (3 min) para esperar dashboard após verificar código
BASE_URL = "https://painel.duoke.com"

# ===== Cripto AES-GCM com PBKDF2 (igual ao seu padrão) =====
# Deriva uma chave a partir de uma senha e salt
def _derive_key(secret: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100_000
    )
    return kdf.derive(secret.encode("utf-8"))

# Criptografa dados com AES-GCM
def encrypt_bytes(data: bytes, secret: str) -> bytes:
    salt = os.urandom(16)
    key = _derive_key(secret, salt)
    aes = AESGCM(key)
    iv = os.urandom(12)
    ct = aes.encrypt(iv, data, None)
    return salt + iv + ct  # retorna o pacote completo

# Descriptografa dados
def decrypt_bytes(data: bytes, secret: str) -> Optional[bytes]:
    try:
        salt = data[:16]
        iv = data[16:28]
        ct = data[28:]
        key = _derive_key(secret, salt)
        aes = AESGCM(key)
        return aes.decrypt(iv, ct, None)
    except Exception as e:
        print(f"Erro de descriptografia: {e}")
        return None

# ===== Playwright (operações de login) =====
app = FastAPI()
PENDING: Dict[str, str] = {} # tentativa_id -> email

def session_path(user_id: str) -> Path:
    return SESS_DIR / f"{user_id}.bin"

def _is_connected(user_id: str) -> bool:
    return session_path(user_id).exists()

async def _do_login(email: str, password: str, phone: str = ""):
    attempt_id = str(uuid.uuid4())
    PENDING[attempt_id] = email
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            ctx = await browser.new_context()
            page = await ctx.new_page()
            await page.goto(f"{BASE_URL}/login")
            
            # Preenche o formulário de login
            await page.fill("input[name='email']", email)
            await page.fill("input[name='password']", password)
            if phone:
                await page.fill("input[name='phone']", phone)

            # Clique no botão de login
            await page.locator("button.login-button").click()

            # Espera até que o botão de "Verificar código" apareça ou a página mude
            await page.wait_for_selector("text=Verificar código", timeout=60000)

            return JSONResponse({
                "ok": True,
                "status": "OTP_SENT",
                "msg": "Código de verificação enviado para o seu email.",
                "attempt_id": attempt_id
            })
        finally:
            await browser.close()

@app.post("/duoke/login")
async def duoke_login(req: Request):
    data = await req.json()
    email = data.get("email")
    password = data.get("password")
    phone = data.get("phone")
    
    if not email or not password:
        raise HTTPException(400, "Email e senha são obrigatórios.")
    
    # Simulação de login, no mundo real você faria o web scraping aqui
    return await _do_login(email, password, phone)

@app.post("/duoke/verify")
async def duoke_verify(req: Request):
    data = await req.json()
    code = data.get("code")
    attempt_id = data.get("attempt_id")
    
    if not code or not attempt_id:
        raise HTTPException(400, "Código de verificação e ID da tentativa são obrigatórios.")
        
    email = PENDING.get(attempt_id)
    if not email:
        raise HTTPException(404, "Tentativa de login expirada ou não encontrada.")
        
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            # Reabre a página de login para verificar o código
            ctx = await browser.new_context()
            page = await ctx.new_page()
            await page.goto(f"{BASE_URL}/login")

            # Preenche o código OTP (seletor genérico para inputs de código/tel)
            sel_code = "input[placeholder*='verification' i], input[type='tel']"
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
            session_path(email).write_bytes(enc) # Use o email como user_id
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
    return {"connected": _is_connected(user_id)}

@app.post("/duoke/logout")
async def duoke_logout(req: Request):
    user_id = (await req.json()).get("user_id")
    if not user_id:
        raise HTTPException(400, "ID do usuário é obrigatório.")
        
    path = session_path(user_id)
    if path.exists():
        path.unlink()
    return JSONResponse({"ok": True})
