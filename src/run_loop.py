# src/run_loop.py
import os
import asyncio
from pathlib import Path
from .duoke import DuokeBot
from .classifier import decide_reply

DEFAULT_INTERVAL = float(os.getenv("LOOP_INTERVAL_SECONDS", "5"))
STATE_FILE = Path(__file__).resolve().parents[1] / "storage_state.json"

async def run_forever(interval: float = DEFAULT_INTERVAL) -> None:
    """
    Executa ciclos do bot indefinidamente.
    Em caso de erro, faz backoff exponencial até 60s e continua tentando.
    CTRL+C (KeyboardInterrupt) encerra com segurança.
    """
    bot = DuokeBot()
    backoff = interval

    while True:
        try:
            await bot.run_once(decide_reply)
            # ciclo OK: reseta backoff e espera intervalo normal
            backoff = interval
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            # encerramento gracioso (quando cancelado por algum orchestrator)
            break
        except KeyboardInterrupt:
            # permite sair com CTRL+C quando rodando standalone
            print("\n[LOOP] Encerrado pelo usuário (CTRL+C).")
            break
        except Exception as e:
            print(f"[LOOP] erro: {e!r}")
            # aguarda com backoff antes de tentar de novo
            wait = min(max(2.0, backoff), 60.0)
            print(f"[LOOP] aguardando {wait:.1f}s antes de tentar novamente...")
            await asyncio.sleep(wait)
            backoff = min(wait * 2, 60.0)

async def main() -> None:
    if not STATE_FILE.exists():
        print("[LOOP] Sessão não encontrada. Execute `python -m src.login` para fazer login antes de iniciar o bot.")
        return
    await run_forever()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # segurança extra caso o KeyboardInterrupt não seja pego dentro do loop
        print("\n[MAIN] Encerrado pelo usuário.")


