FROM python:3.11

WORKDIR /app

# Linux bağımlılıkları (Playwright için gerekli)
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    libnss3 \
    libatk-bridge2.0-0 \
    libgtk-3-0 \
    libgbm1 \
    libxss1 \
    libasound2 \
    libxshmfence1 \
    libx11-xcb1 \
    libxcb-dri3-0 \
    libdrm2 \
    libxdamage1 \
    libxrandr2 \
    libxcomposite1 \
    libxfixes3 \
    libpango-1.0-0 \
    libcairo2 \
    libatk1.0-0 \
    libatspi2.0-0 \
    fonts-liberation \
    --no-install-recommends

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwright browser install
RUN playwright install chromium

COPY . .

CMD ["uvicorn","app:app","--host","0.0.0.0","--port","8080"]