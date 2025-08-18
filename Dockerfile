# Usa imagem com Playwright + browsers já instalados
FROM mcr.microsoft.com/playwright/python:v1.46.0-jammy

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Cache melhor do pip
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copia o código
COPY . .

# Porta padrão (Render passa $PORT em runtime)
ENV PORT=10000

# Start: respeita $PORT e aponta para o módulo correto
CMD ["sh","-c","uvicorn src.app_ui:app --host 0.0.0.0 --port ${PORT:-10000}"]

