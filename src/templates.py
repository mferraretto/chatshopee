import json, os
from pathlib import Path

def load_templates() -> dict:
    p = Path(__file__).resolve().parents[1] / "templates" / "templates.json"
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)

# Carrega automaticamente ao importar
TEMPLATES = load_templates()
