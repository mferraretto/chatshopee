# Base leve do Python
FROM python:3.11-slim

# Evita caches e prompts do apt
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# Alguns utilitários + fontes (evita o "waiting for fonts to load...")
RUN apt-get update && apt-get install -y \
    curl wget ca-certificates gnupg \
    fonts-liberation fonts-noto fontconfig \
    && rm -rf /var/lib/apt/lists/*

# Instala dependências Python do seu projeto
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Instala Playwright e BAIXA os browsers dentro da imagem
# (usa --with-deps para instalar libs do Chromium no Debian slim)
RUN pip install --no-cache-dir playwright==1.46.0 && \
    playwright install --with-deps chromium

# Copia o restante do código
COPY . .

# Porta (o Render injeta $PORT em runtime)
ENV PORT=10000

# Sobe sua API (ajuste o módulo se for diferente)
CMD ["sh","-c","uvicorn src.app_ui:app --host 0.0.0.0 --port ${PORT:-10000}"]
