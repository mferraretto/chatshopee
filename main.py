# main.py
from fastapi import FastAPI, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from pathlib import Path
from playwright.async_api import async_playwright, TimeoutError as PWTimeoutError
import json

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://SEU-DOMINIO.render.com", "http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

SESS_DIR = Path("sessions")
SESS_DIR.mkdir(exist_ok=True)

async def playwright_login_duoke(email: str, password: str):
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = await browser.new_context()
        page = await ctx.new_page()

        # Abra o Duoke (ajuste se houver URL de login direta)
        await page.goto("https://www.duoke.com/", wait_until="domcontentloaded")

        # Feche popup de sessão expirada (se aparecer)
        try:
            await page.get_by_role("button", name="Confirm").click(timeout=2000)
        except PWTimeoutError:
            pass

        # Preencha credenciais (ajuste seletores se necessário)
        email_sel = "input[type='email'], input[placeholder='Email']"
        pass_sel  = "input[type='password'], input[placeholder='Password']"
        await page.fill(email_sel, email)
        await page.fill(pass_sel, password)
        await page.get_by_role("button", name="Login").click()

        # Aguarde o pós-login; se o Duoke tiver 2FA/captcha, aqui você pode
        # detectar e retornar um erro específico pedindo ação do usuário.
        await page.wait_for_load_state("networkidle")

        # Salve o storage_state na memória e finalize
        state = await ctx.storage_state()
        await browser.close()
        return state

def sess_path(uid: str) -> Path:
    return SESS_DIR / f"{uid}.json"

@app.post("/duoke/connect")
async def duoke_connect(uid: str = Form(...), email: str = Form(...), password: str = Form(...)):
    # TODO: valide UID (ex.: verificar ID token do Firebase) antes de aceitar
    state = await playwright_login_duoke(email, password)
    sess_path(uid).write_text(json.dumps(state), encoding="utf-8")
    return {"ok": True}

@app.get("/duoke/status")
def duoke_status(uid: str):
    return {"connected": sess_path(uid).exists()}

@app.delete("/duoke/connect")
def duoke_disconnect(uid: str):
    p = sess_path(uid)
    if p.exists():
        p.unlink()
    return {"ok": True}

# Exemplo de uso do estado salvo numa automação
@app.get("/duoke/ping")
async def duoke_ping(uid: str):
    p = sess_path(uid)
    if not p.exists():
        raise HTTPException(401, "Duoke não conectado")
    state = json.loads(p.read_text(encoding="utf-8"))

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = await browser.new_context(storage_state=state)
        page = await ctx.new_page()
        await page.goto("https://www.duoke.com/", wait_until="domcontentloaded")
        url = page.url
        await browser.close()
    return {"ok": True, "url": url}
