# src/run_once.py
import asyncio
from .duoke import DuokeBot
from .classifier import decide_reply

async def main():
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

