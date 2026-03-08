FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# CMD is intentionally omitted — overridden per-service in docker-compose.yml
