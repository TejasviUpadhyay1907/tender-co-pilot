# Tender Co-Pilot backend image.
# Build from the REPO ROOT so backend/ paths resolve, e.g.:
#   docker build -f backend/Dockerfile -t tender-copilot .
FROM python:3.11-slim

# gcc/g++ needed to build some wheels (e.g. chromadb dependencies)
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc g++ \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# Copy the whole repo (app, seed/, frontend build output, etc.)
COPY . /app

ENV PYTHONUNBUFFERED=1 \
    HF_HOME=/app/data/hf

EXPOSE 8000

# Render (and similar hosts) inject $PORT; fall back to 8000 locally.
CMD ["sh", "-c", "uvicorn backend.app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
