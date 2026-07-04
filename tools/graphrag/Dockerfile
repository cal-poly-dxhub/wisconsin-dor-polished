FROM python:3.13-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends poppler-utils gcc g++ && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY tools/graphrag/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt && \
    apt-get purge -y --auto-remove gcc g++ || true

COPY tools/graphrag/ /app/tools/graphrag/
COPY tools/pdf_chunking/ /app/tools/pdf_chunking/

ENV PYTHONPATH=/app/tools:/app/tools/graphrag:/app

COPY tools/graphrag/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]
