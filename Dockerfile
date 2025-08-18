FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

# 1) Dependências do sistema compatíveis com Debian 12/Bookworm
# (sem ttf-unifont/ttf-ubuntu-font-family)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates wget unzip \
    libglib2.0-0 libnss3 libnspr4 \
    libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxrandr2 \
    libgbm1 libasound2 \
    libpangocairo-1.0-0 libpango-1.0-0 libcairo2 \
    fonts-liberation fonts-unifont fonts-ubuntu fonts-noto-color-emoji \
  && rm -rf /var/lib/apt/lists/*

# 2) Dependências Python
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir playwright==1.46.0

# 3) Baixa somente o Chromium (sem tentar instalar deps via APT)
RUN python -m playwright install chromium

# 4) Copia o app
COPY . .

# 5) Porta e comando
ENV PORT=10000
CMD ["uvicorn", "app_ui:app", "--host", "0.0.0.0", "--port", "10000"]
