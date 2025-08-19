# src/run_once.py
import asyncio
from pathlib import Path
from .duoke import DuokeBot
from .classifier import decide_reply

STATE_FILE = Path(__file__).resolve().parents[1] / "storage_state.json"

async def main():
    if not STATE_FILE.exists():
        print("[RUN_ONCE] Sessão não encontrada. Execute `python -m src.login` para fazer login antes de iniciar o bot.")
        return
    bot = DuokeBot()

    # Função síncrona (NÃO async) para evitar "coroutine was never awaited"
    def debug_reply(messages: list[str]) -> tuple[bool, str]:
        print("[DEBUG] Mensagens recebidas para classificação:")
        for msg in messages:
            print("-", msg)
        should, reply = decide_reply(messages)
        print(f"[DEBUG] Deve responder? {should} | Resposta: {reply}")
        return should, reply

    await bot.run_once(debug_reply)

if __name__ == "__main__":
    asyncio.run(main())

