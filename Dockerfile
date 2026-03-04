FROM python:3.11

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwright browser + dependencies
RUN playwright install chromium
COPY . .

CMD ["bash","-lc","uvicorn app:app --host 0.0.0.0 --port ${PORT:-8080}"]