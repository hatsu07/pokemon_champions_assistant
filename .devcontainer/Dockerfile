FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install --no-install-recommends -y adb tesseract-ocr tesseract-ocr-jpn libdbus-1-3 libegl1 libgl1 libxkbcommon0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
COPY requirements-baseline.txt /tmp/requirements-baseline.txt
RUN pip install --no-cache-dir -r /tmp/requirements-baseline.txt
