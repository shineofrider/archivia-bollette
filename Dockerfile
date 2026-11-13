FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

# Browser dependencies are installed in the image, not on the OMV host.
RUN pip install --no-cache-dir playwright pyyaml imapclient requests pypdf
RUN python -m playwright install --with-deps chromium

COPY . /app

CMD ["python", "-m", "app"]
