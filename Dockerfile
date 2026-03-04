FROM mcr.microsoft.com/playwright/python:v1.49.1-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["bash","-lc","uvicorn app:app --host 0.0.0.0 --port ${PORT:-8080}"]