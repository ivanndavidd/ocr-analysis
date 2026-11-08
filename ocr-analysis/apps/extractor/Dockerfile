# Batch CLI image: Python 3.11 + Tesseract 5 (ind+eng), no GPU, no server, no port.
# Build and run commands are in AGENTS.md `## Build and run`.

FROM python:3.11.15-slim-trixie AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /src
RUN python -m venv /opt/venv
COPY pyproject.toml requirements.lock ./
COPY src ./src
# Runtime dependencies only: the lock pins every version, and the dev extra (pytest) is never requested.
RUN /opt/venv/bin/pip install --constraint requirements.lock .

FROM python:3.11.15-slim-trixie AS runtime
# Patch the base OS packages, then add Tesseract with the Indonesian and English data (tessdata_fast 4.1.0).
# OCR versions are pinned because page selection and signature thresholds are calibrated against their output.
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
        tesseract-ocr=5.5.0-1 \
        tesseract-ocr-ind=1:4.1.0-2 \
        tesseract-ocr-eng=1:4.1.0-2 \
    && rm -rf /var/lib/apt/lists/*
RUN groupadd --gid 10001 extractor \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin extractor \
    && mkdir -p /data/in /data/out \
    && chown 10001:10001 /data/out
COPY --from=build /opt/venv /opt/venv
# HOME and TMPDIR point at /tmp so the container runs with a read-only root and a tmpfs /tmp:
# pytesseract writes each page image to a temp file, which then never touches a persistent disk.
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    TMPDIR=/tmp
WORKDIR /data
USER 10001:10001
HEALTHCHECK NONE
ENTRYPOINT ["contract-extractor"]
CMD ["run", "/data/in", "--out", "/data/out"]
