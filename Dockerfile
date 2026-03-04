FROM python:3.11

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwright browser install
RUN playwright install chromium

COPY . .

CMD ["uvicorn","app:app","--host","0.0.0.0","--port","8080"]