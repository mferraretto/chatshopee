# src/classifier.py
from typing import Tuple, List
from .templates import load_templates
from .gemini_client import classify
import unicodedata, re

TEMPLATES = load_templates()
SENTINEL_TAG_GPT = "__TAG_GPT__"  # usado para sinalizar: etiquetar e pular no duoke.py

# ===== Respostas prontas exigidas =====
BREAKAGE_TEXT = (
    "Olá! Sentimos muito pelo ocorrido. Podemos resolver de 3 formas:\n"
    " Reembolso parcial — você fica com o produto e recebe parte do valor de volta.\n"
    " Devolução pelo app da Shopee — com reembolso total após o retorno.\n"
    " Envio de nova peça — sem custo pela peça, você paga apenas o frete, e não precisa devolver nada.\n"
    "Me avisa qual opção prefere que resolvo tudo por aqui!"
)

MISSING_TEXT = (
    "Oii, tudo bem? Peço desculpas por isso, posso estar te enviando a peça que faltou, "
    "ou se preferir posso fazer seu reembolso, o que você prefere?"
)

def _normalize(s: str) -> str:
    s = s.lower().strip()
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    s = re.sub(r"\s+", " ", s)
    return s

def _t(key: str, fallback_key: str = "default", fallback_text: str = "Obrigado pela mensagem! 😊"):
    return TEMPLATES.get(key) or TEMPLATES.get(fallback_key) or fallback_text

# Vocabulário
RE_NAO = r"(?:n[oã]o|nao)"
RE_FOTO = r"\b(foto|fotos|imagem|imagens|anexo|anexei|segue foto|enviei foto|mandei foto|em anexo)\b"
RE_FRUSTRACAO = r"(desde ontem|repetindo a mesma coisa|dif[ií]cil|ningu[eé]m resolve|cansei|uai|v[cs] est[aã]o demorando|est[aã] demorando)"

RE_QUEBRA = (
    r"(?:\bquebrad[ao]\b|\btrincad[oa]\b|\brachad[oa]\b|\bamassad[oa]\b|\briscad[oa]\b|"
    r"\blascad[oa]\b|\bempenad[oa]\b|\bdeformad[oa]\b|\bavariad[oa]\b|\bdanificad[oa]\b|"
    r"\bveio\s*estragado\b|\bdefeit[oa]\b|\bnao funciona\b|\bdefeito de fabrica\b|\bcom problema\b)"
)

RE_FALTANDO = (
    r"(?:\bfaltou\b|\bveio\s*faltando\b|\bnao\s*veio\b|\bpe[cç]a faltando\b|\bitem faltando\b|"
    r"\bparafuso[s]?\s*faltando\b|\bsem\s+(?:pe[cç]a|item|parafuso[s]?|acess[óo]rio[s]?|componente[s]?)\b|"
    r"\bkit\s+incompleto\b|\bincompleto\b|\bn[ãa]o veio tudo\b)"
)

RE_ENVIO = r"(rastreio|rastreamento|codigo de rastreio|c[oó]digo de rastreio|enviado|envio|postado|transportadora|chega quando|prazo de entrega|a caminho)"

# Casos para pular (além de PIX)
RE_COBRANCA_PECA_NAO_ENVIADA = r"(ainda\s+nao\s*(?:foi|foram)\s*enviad[oa]s?\s*(?:a|as)\s*pe[cç]a[s]?|ainda\s+nao\s*enviaram\s*(?:a|as)\s*pe[cç]a[s]?)"

def decide_reply(messages: List[str]) -> Tuple[bool, str]:
    if not messages:
        return (False, "")

    last = _normalize(messages[-1])
    full = _normalize(" ".join(messages))

    # ignorar interjeições muito vagas
    if last in {"?", "??", "???", "????"}:
        return (False, "")

    # ignorar reclamações “PIX/reembolso não caiu”
    if re.search(rf"\b(pix|reembolso)\b.*?\b{RE_NAO}\b.*?\b(caiu|recebi|entrou)\b", full):
        return (False, "")

    # ignorar cobranças de "ainda não enviaram a peça que faltou"
    if re.search(RE_COBRANCA_PECA_NAO_ENVIADA, full):
        return (False, "")

    # ======== VALORES / POLÍTICA ========
    # a) valor do reembolso parcial (responde 30%)
    if re.search(r"(qual|quanto).{0,20}valor.{0,20}reembolso\s*parcial", full) or \
       re.search(r"\breembolso\s*parcial\b.*\b(valor|quanto)\b", full):
        return (True, _t("valor_reembolso_parcial", fallback_key="reembolso_parcial"))

    # b) valor de FRETE para reenvio de nova peça -> etiquetar GPT e pular
    if re.search(r"(qual|quanto).{0,20}valor.{0,20}frete", full) and \
       re.search(r"(nova|outra)\s*pe[cç]a|reenvio|reposi[cç]a?o|enviar outra", full):
        return (False, SENTINEL_TAG_GPT)

    # ======== marcado como recebido, mas não recebi =========
    if re.search(r"(marcou|marcaram|consta|apareceu|colocou|lan[cç]ou).*(recebid[oa]|entregue)", full) and \
       re.search(rf"\b{RE_NAO}\b.*\b(receb[iu]|chegou)\b", full):
        reply = _t("nao_recebido_marcado_recebido", fallback_key="default")
        if re.search(RE_FRUSTRACAO, full):
            reply = "Entendo a frustração com essa situação. 🙏 " + reply
        return (True, reply)

    # ======== urgência para cilindro grande =========
    if re.search(r"\bcilindro\s+grande\b", full) and \
       re.search(r"\burgenc|festa|hoje|amanh[aã]|chegando|preciso que envie|preciso enviar\b", full):
        reply = _t("urgencia_cilindro_grande", fallback_key="envio")
        if re.search(RE_FRUSTRACAO, full):
            reply = "Entendo a urgência e a frustração. 🙏 " + reply
        return (True, reply)

    # ======== reembolso parcial (esperando/querendo) =========
    if re.search(r"\bestou (?:aguardando|esperando).{0,20}reembolso\s*parcial\b", full) or \
       re.search(r"\breembolso\s*parcial\b", last):
        return (True, _t("reembolso_parcial", fallback_key="confirm_reembolso_parcial"))

    # ======== confirmações 3 opções explícitas no texto =========
    if re.search(r"\breembolso\s*parcial\b|\bparcial\b", last):
        return (True, _t("confirm_reembolso_parcial"))
    if re.search(r"\bdevolu[cç]a?o\b|\breembolso\s*total\b", last):
        return (True, _t("confirm_devolucao_total"))
    if re.search(r"\b(nova|outra)\s*pe[cç]a\b|\breenvio\b|\breposi[cç]a?o\b|\benviar\s*outra\b", last):
        return (True, _t("confirm_envio_nova_peca"))

    # ======== faltando peça (resposta pronta) =========
    if re.search(RE_FALTANDO, full):
        return (True, MISSING_TEXT)

    # ======== quebra / defeito (resposta pronta) =========
    if re.search(RE_QUEBRA, full):
        # Se mencionar foto, você pode optar por outra template se quiser:
        # if re.search(RE_FOTO, full): return (True, _t("quebrado_com_foto", fallback_key="quebra_3_opcoes"))
        return (True, BREAKAGE_TEXT)

    # ======== Fallback via Gemini =========
    info = classify(messages) or {}
    intent = (info.get("intent") or "").strip().lower()

    # Não aceite "envio" do modelo se o texto não fala de envio
    if intent == "envio" and not re.search(RE_ENVIO, full):
        intent = "default"

    intent_map = {
        "tempo_envio": "tempo_envio",
        "quebrado_com_foto": "quebrado_com_foto",
        "quebrado_sem_foto": "quebrado_sem_foto",
        "quebra": "quebra_3_opcoes",
        "faltando": "faltando_peca",
        "faltando_peca": "faltando_peca",
        "reembolso_parcial": "reembolso_parcial",
        "devolucao_total": "devolucao_total",
        "pedido_cancelado": "pedido_cancelado",
        "pedido_parado": "pedido_parado",
        "cilindro_pequeno": "cilindro_pequeno",
        "elogio": "elogio",
        "envio": "envio",
        "agradecimento": "agradecimento_generico",
        "nao_recebido_marcado_recebido": "nao_recebido_marcado_recebido",
        "urgencia_cilindro_grande": "urgencia_cilindro_grande",
        "default": "default",
        "pular": None,
    }

    key = intent_map.get(intent, intent)
    if key is None:
        return (False, "")
    return (True, _t(key, fallback_key="envio"))
