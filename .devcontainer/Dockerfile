FROM python:3.12-slim AS dev

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install --no-install-recommends -y adb tesseract-ocr tesseract-ocr-jpn libdbus-1-3 libegl1 libgl1 libxkbcommon0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
COPY requirements-baseline.txt /tmp/requirements-baseline.txt
RUN pip install --no-cache-dir -r /tmp/requirements-baseline.txt

# Keep the large, optional model-training stack out of the ordinary development
# image.  The Compose `trainer` service explicitly selects this stage.
FROM dev AS trainer

COPY requirements-training.txt /tmp/requirements-training.txt
RUN pip install --no-cache-dir -r /tmp/requirements-training.txt

# CUDA stays isolated from both the ordinary dev and CPU trainer images.
FROM dev AS trainer-gpu

COPY requirements-training-gpu.txt /tmp/requirements-training-gpu.txt
RUN pip install --no-cache-dir -r /tmp/requirements-training-gpu.txt

# Preserve the lean development image as the default target for direct builds.
FROM dev AS default
