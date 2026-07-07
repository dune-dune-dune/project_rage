# Build context is the repository root (see docker-compose.yml) so the
# rws_control.py protocol library can be copied in and reused as-is.
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY services/web/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Protocol library (repo root) reused for packet build + checksum.
COPY rws_control.py ./rws_control.py
COPY services/web/settings.toml ./settings.toml
COPY services/web/gunicorn.conf.py ./gunicorn.conf.py
COPY services/web/app ./app

EXPOSE 8000

# gunicorn.conf.py pins workers=1 (sole owner of the UDP channel + sequence counter).
CMD ["gunicorn", "-c", "gunicorn.conf.py", "app.wsgi:app"]
