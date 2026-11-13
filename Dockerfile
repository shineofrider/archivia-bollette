FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

LABEL org.opencontainers.image.source="https://github.com/shineofrider/archivia-bollette"

# libatomic1 is needed by Playwright's bundled Node runtime.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tzdata libatomic1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt \
    && python -m playwright install --with-deps chromium \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY app /app/app
COPY config/config.yaml /config/config.yaml

RUN mkdir -p /data /archive/Corrente /archive/Gas /tmp/iren \
    && chmod 755 /app

CMD ["python", "-m", "app"]
