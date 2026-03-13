FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-fin \
    && rm -rf /var/lib/apt/lists/*

COPY admin/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

COPY admin/runtime/ ./
COPY admin/ui ./admin

CMD ["python", "admin_server.py"]
