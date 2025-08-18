# src/login.py
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright
from .config import settings

PROFILE_DIR = Path(__file__).resolve().parents[1] / ".playwright_profile"
STATE_FILE = Path(__file__).resolve().parents[1] / "storage_state.json"

async def main():
    PROFILE_DIR.mkdir(exist_ok=True)
    async with async_playwright() as p:
        # Abrir navegador visível (headful) com perfil persistente só para facilitar o login
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
        )
        page = await ctx.new_page()
        await page.goto(settings.douke_url)
        print(">>> Faça login no Douke no navegador aberto.")
        input(">>> Quando terminar o login e enxergar suas conversas, pressione Enter aqui... ")
        # Exporta a sessão para storage_state.json (vamos usar isso nos runs)
        await ctx.storage_state(path=str(STATE_FILE))
        print(f">>> Sessão salva em: {STATE_FILE}")
        await ctx.close()

if __name__ == "__main__":
    asyncio.run(main())
