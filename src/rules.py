# src/rules.py
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional, Tuple, List, Dict

# Caminho do rules.json na raiz do projeto
RULES_PATH = (Path(__file__).resolve().parents[1] / "rules.json")

def _ensure_rules_file_exists() -> None:
    """Cria um rules.json básico se não existir."""
    if RULES_PATH.exists():
        return
    RULES_PATH.write_text(
        json.dumps({"version": 1, "rules": []}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

def load_rules() -> List[Dict]:
    """
    Lê e retorna a lista de regras (campo 'rules') do rules.json.
    Tolera JSON inválido retornando lista vazia (e mantendo o servidor vivo).
    """
    _ensure_rules_file_exists()
    try:
        data = json.loads(RULES_PATH.read_text(encoding="utf-8"))
        # Aceita tanto {"rules":[...]} quanto uma lista direta [...]
        if isinstance(data, dict) and "rules" in data and isinstance(data["rules"], list):
            return data["rules"]
        if isinstance(data, list):
            return data
        return []
    except Exception as e:
        # Log simples; você pode trocar por logging se preferir
        print(f"[rules] Aviso: falha ao carregar {RULES_PATH.name}: {e}")
        return []

def save_rules(rules: List[Dict]) -> None:
    """
    Salva as regras. Envolve dentro de {"version":X,"rules":[...]} para manter evolutivo.
    """
    payload = {"version": 1, "rules": rules}
    RULES_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

def _text_matches(
    texts: List[str],
    any_contains: List[str] | None = None,
    all_contains: List[str] | None = None,
    any_regex: List[str] | None = None,
) -> bool:
    """Verifica se o contexto bate com as condições declarativas da regra."""
    # normaliza tudo para lowercase
    texts_l = [t.lower() for t in texts]

    if any_contains:
        needles = [n.lower() for n in any_contains]
        if not any(any(n in t for n in needles) for t in texts_l):
            return False

    if all_contains:
        needles = [n.lower() for n in all_contains]
        if not all(any(n in t for t in texts_l) for n in needles):
            return False

    if any_regex:
        try:
            patterns = [re.compile(p, re.I | re.S) for p in any_regex]
        except re.error as e:
            print(f"[rules] regex inválida em any_regex: {e}")
            return False
        if not any(p.search(t) for p in patterns for t in texts_l):
            return False

    return True

def apply_rules(messages: List[str]) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Aplica regras ao contexto curto (últimas 5 mensagens).
    Retorna (decide, reply, action):
      - decide=False, reply=None, action='skip'  -> não responder (pular)
      - decide=True,  reply=str,  action='reply' -> responder com 'reply'
      - decide=False, reply=None, action=None    -> nenhuma regra casou (deixa o caller decidir)
    """
    rules = load_rules()
    if not messages:
        return False, None, None

    last_user_texts = messages[-5:]  # contexto curto

    for rule in rules:
        if not rule.get("active", True):
            continue

        cond = rule.get("match", {}) or {}
        if not _text_matches(
            texts=last_user_texts,
            any_contains=cond.get("any_contains"),
            all_contains=cond.get("all_contains"),
            any_regex=cond.get("any_regex"),
        ):
            continue

        action = (rule.get("action") or "").strip().lower()
        if action == "skip":
            return False, None, "skip"

        reply = rule.get("reply")
        if isinstance(reply, str) and reply.strip():
            return True, reply, "reply"

        # Caso a regra case mas não tenha reply nem 'skip', tratamos como "sem decisão"
        return False, None, None

    # Nenhuma regra casou
    return False, None, None

