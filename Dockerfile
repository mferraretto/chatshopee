FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# 1) Bibliotecas nativas necessárias pro Chromium + fontes (evitam travas de “waiting for fonts”)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl wget gnupg \
    # libs de runtime
    libasound2 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdbus-1-3 \
    libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
    libgtk-3-0 libnspr4 libnss3 libwayland-client0 libxshmfence1 \
    libx11-6 libx11-xcb1 libxcb1 libxext6 libxss1 libexpat1 \
    libgbm1 libglib2.0-0 libpango-1.0-0 libpangocairo-1.0-0 \
    # fontes
    fonts-noto fonts-noto-color-emoji fonts-liberation fonts-unifont fontconfig \
    && rm -rf /var/lib/apt/lists/*

# 2) Python deps
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 3) Playwright + navegadores (sem --with-deps para não chamar o script de Ubuntu)
RUN pip install --no-index "playwright==1.46.0" && playwright install chromium

# 4) Código da sua aplicação
COPY . .

# 5) A porta padrão do FastAPI, por padrão o uvicorn expõe a 8000
EXPOSE 8000

CMD ["uvicorn", "app_ui:app", "--host", "0.0.0.0", "--port", "8000"]
