import asyncio, json

queue: asyncio.Queue[str] = asyncio.Queue()

async def emit(event: str, **data):
    """Empurra um evento JSON na fila para a UI ler via WebSocket."""
    payload = {"event": event, **data}
    await queue.put(json.dumps(payload, ensure_ascii=False))
