# The Leadline API and its Temporal worker: one image, two commands (see deploy/).
#   docker build -t leadline-backend .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DJANGO_SETTINGS_MODULE=config.settings.production \
    KNOWLEDGE_EMBEDDING_CACHE_DIR=/app/.cache/fastembed

WORKDIR /app

# libpq5 for psycopg2 at runtime; curl for the health check.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements/ requirements/
RUN pip install -r requirements/production.txt

# The embedding model (~70 MB), so the server never downloads it at first use. Keep in
# step with KNOWLEDGE_EMBEDDING_MODEL.
ARG EMBEDDING_MODEL=BAAI/bge-small-en-v1.5
RUN python -c "from fastembed import TextEmbedding; \
TextEmbedding(model_name='${EMBEDDING_MODEL}', cache_dir='/app/.cache/fastembed')"

COPY . .

# Collected at build time: the admin and API docs, served by WhiteNoise. Placeholder
# settings: nothing connects or signs here.
RUN DJANGO_SECRET_KEY=build DJANGO_ALLOWED_HOSTS=build DB_NAME=x DB_USER=x DB_PASSWORD=x \
    EMAIL_HOST=x EMAIL_HOST_USER=x EMAIL_HOST_PASSWORD=x \
    python manage.py collectstatic --no-input -v 0

RUN useradd --uid 1000 --create-home leadline \
    && mkdir -p /app/media \
    && chown -R leadline:leadline /app/media /app/.cache
USER leadline

EXPOSE 8000
CMD ["docker/start-api.sh"]
