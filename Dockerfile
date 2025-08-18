# Usa imagem oficial do Python
FROM python:3.12-slim

# Evita buffering de logs
ENV PYTHONUNBUFFERED=1

# Cria diretório de trabalho
WORKDIR /app

# Copia dependências
COPY requirements.txt .

# Instala dependências do sistema + playwright
RUN apt-get update && apt-get install -y wget gnupg unzip fonts-liberation libatk1.0-0 \
    libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 libxdamage1 libxrandr2 \
    libgbm1 libasound2 libpangocairo-1.0-0 libpango-1.0-0 libcairo2 libnss3 libxcomposite1 \
    && pip install --no-cache-dir -r requirements.txt \
    && pip install playwright==1.46.0 \
    && python -m playwright install --with-deps chromium

# Copia todo o código
COPY . .

# Render expõe $PORT, então usamos
ENV PORT=8000

# Comando de start
CMD ["uvicorn", "app_ui:app", "--host", "0.0.0.0", "--port", "8000"]
