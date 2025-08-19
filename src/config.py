import os
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

class Settings(BaseModel):
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    douke_url: str = os.getenv("DOUKE_URL", "https://web.duoke.com/?lang=en#/dk/main/chat")
    max_conversations: int = int(os.getenv("MAX_CONVERSATIONS", "50"))
    history_depth: int = int(os.getenv("HISTORY_DEPTH", "8"))
    apply_needs_reply_filter: bool = os.getenv("APPLY_NEEDS_REPLY_FILTER", "nao").lower() in ("sim","yes","true","1")
    loop_interval: int = int(os.getenv("LOOP_INTERVAL", "30"))
    delay_after_nav: float = float(os.getenv("DELAY_AFTER_NAV", "1"))
    delay_between_actions: float = float(os.getenv("DELAY_BETWEEN_ACTIONS", "0.1"))
    goto_timeout_ms: int = int(os.getenv("GOTO_TIMEOUT_MS", "60000"))

settings = Settings()
