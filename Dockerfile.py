# ---------- Dockerfile ----------
# Python leve
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Dependências de sistema do Chromium (Playwright)
RUN apt-get update && apt-get install -y \
    libglib2.0-0 libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 \
    libxfixes3 libxrandr2 libasound2 libatspi2.0-0 libpangocairo-1.0-0 \
    libpango-1.0-0 libcairo2 libgbm1 fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# Instala dependências Python
COPY requirements.txt .
RUN pip install -r requirements.txt

# Instala o Chromium do Playwright (com deps)
RUN python -m playwright install --with-deps chromium

# Copia o app
COPY . .

# Render injeta $PORT; subimos o uvicorn servindo o app_ui:app
ENV HOST=0.0.0.0
CMD uvicorn app_ui:app --host $HOST --port ${PORT:-8000}
# ---------- fim ----------
