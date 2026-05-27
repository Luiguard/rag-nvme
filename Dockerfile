FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV RAG_BIND_HOST=0.0.0.0
ENV RAG_MAX_WORKERS=8
ENV RAG_MAX_CONCURRENT=200

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8090

ENTRYPOINT ["python", "rag_server_async.py"]
