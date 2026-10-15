FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends fontconfig fonts-liberation fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# .dockerignore keeps .env / token.json / client_secret.json out of the image. Pass secrets at runtime:
#   docker run --env-file .env yt-horror
COPY . .
CMD ["python", "run_daily.py"]
