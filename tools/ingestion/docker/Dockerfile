FROM python:3.13-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends poppler-utils gcc g++ && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY tools/ingestion/docker/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt && \
    apt-get purge -y --auto-remove gcc g++ || true

# The pipeline imports as `tools.ingestion.*`, so copy the package under
# /app/tools/ and put /app on the path. Copy tools/__init__.py so `tools` is a
# regular package (matches the local repo layout).
COPY tools/__init__.py /app/tools/__init__.py
COPY tools/ingestion/ /app/tools/ingestion/

ENV PYTHONPATH=/app

COPY tools/ingestion/docker/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]
