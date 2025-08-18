import google.generativeai as genai
from .config import settings

def get_gemini():
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY ausente. Configure no .env")
    genai.configure(api_key=settings.gemini_api_key)
    # modelo leve e rápido para classificação
    return genai.GenerativeModel("gemini-1.5-flash")

PROMPT = """
Você é um classificador e gerador de resposta para atendimento Shopee.
REGRAS IMPORTANTES:
- Leia as últimas mensagens (histórico) do comprador e do vendedor.
- Se for reclamação de PIX não recebido OU cobrança de peça prometida ainda não enviada, responda com: {"intent": "pular"} (não responder).
- Se for quebra/defeito: intent = "quebra"
- Se for peça faltando: intent = "faltando"
- Se for elogio/recebimento: intent = "elogio"
- Se for dúvida geral (prazo, rastreio, etc.): intent = "envio"
- Caso não tenha certeza, tente "envio" (neutro) — mas nunca responda se for sobre PIX/peça prometida não enviada.

Saída EXCLUSIVAMENTE em JSON:
{
  "intent": "<quebra|faltando|elogio|envio|pular>",
  "reason": "<motivo da classificação em 1 frase>",
  "needs_reply": true/false
}
"""

def classify(messages: list[str]) -> dict:
    model = get_gemini()
    history = "\n".join(messages[-8:])
    inp = f"{PROMPT}\n\nHISTORICO:\n{history}"
    try:
        resp = model.generate_content(inp)
        txt = resp.text.strip()
    except Exception as e:
        # fallback se Gemini falhar
        return _fallback_classify(messages)
    import json
    try:
        data = json.loads(txt)
        assert isinstance(data, dict)
        return data
    except Exception:
        return _fallback_classify(messages)

def _fallback_classify(messages: list[str]) -> dict:
    t = " ".join(messages[-8:]).lower()
    def has(*keys): 
        return any(k in t for k in keys)
    if has("pix","não recebi o pix","cadê o pix","pix não caiu","reembolso não caiu"):
        return {"intent":"pular","reason":"pix/reembolso pendente","needs_reply":False}
    if has("quebrou","quebrado","trincado","amassado","danificado","rachado"):
        return {"intent":"quebra","reason":"dano relatado","needs_reply":True}
    if has("faltou","não veio","sem parafuso","faltando"):
        return {"intent":"faltando","reason":"item faltante","needs_reply":True}
    if has("chegou certinho","amei","perfeito","obrigado","obrigada"):
        return {"intent":"elogio","reason":"elogio/recebido","needs_reply":True}
    if has("prazo","quando chega","não chegou","rastreamento","código"):
        return {"intent":"envio","reason":"dúvida logística","needs_reply":True}
    return {"intent":"envio","reason":"fallback neutro","needs_reply":True}
