# Usa a imagem oficial do Playwright com browsers já instalados
FROM mcr.microsoft.com/playwright/python:v1.46.0-jammy

# Impede Python de gerar .pyc e usa stdout sem buffer
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    # Playwright/Chromium flags para Render (sem sandbox)
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PYPPETEER_EXECUTABLE_PATH=/ms-playwright/chromium-1124/chrome-linux/chrome

WORKDIR /app

# Só copie os arquivos que mudam pouco para cachear melhor
COPY requirements.txt /app/

# Instala as libs Python do seu projeto
RUN pip install --no-cache-dir -r requirements.txt

# Instala fontes comuns para evitar bloqueios de carregamento
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-liberation fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/*

# Copia o resto do código
COPY . /app

# Porta do Render
ENV PORT=10000

# Comando de start (Uvicorn com a sua app)
CMD ["uvicorn", "app_ui:app", "--host", "0.0.0.0", "--port", "10000"]
