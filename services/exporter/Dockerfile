FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # Ultralytics writes settings/fonts on first run; without a writable HOME it
    # falls back to paths that do not exist in a slim image.
    YOLO_CONFIG_DIR=/tmp/ultralytics \
    MPLCONFIGDIR=/tmp/matplotlib

# opencv (an ultralytics dependency) needs libGL/libglib even headless.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Its own layer: torch is a slow, ~GB install and must not be re-run on every
# code change (the Jetson builds this image itself — see .github/workflows/deploy.yml).
COPY services/exporter/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY services/exporter/src ./src

EXPOSE 8901

# A single worker: exports are serialised on purpose so two of them cannot fight
# over the CPU the turret loop needs. The timeout is generous — a Jetson-CPU YOLO
# export is minutes, and unlike the cockpit this process is not safety-critical.
CMD ["gunicorn", "--bind", "0.0.0.0:8901", "--workers", "1", "--threads", "2", \
     "--timeout", "1800", "src.main:app"]
