FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Hugging Face Spaces (and most container hosts) run the image as uid 1000,
# so /app has to belong to that user or the session and cache writes fail.
RUN useradd -m -u 1000 app
WORKDIR /app

COPY --chown=app:app requirements.txt .
RUN pip install -r requirements.txt

COPY --chown=app:app . .

# Seed the runtime cache from the committed snapshot so a fresh container
# (no persistent disk on free hosting tiers) starts warm instead of having
# to re-scrape every film from cold on every deploy.
RUN test -f film_cache.seed.json && cp film_cache.seed.json film_cache.json || true
RUN test -f people_cache.seed.json && cp people_cache.seed.json people_cache.json || true

# Written at runtime: one CSV per upload, plus the shared film cache.
RUN mkdir -p /app/sessions && chown -R app:app /app
USER app

# server.py reads PORT and purges expired sessions on boot, so it is the entry
# point rather than a bare `uvicorn` command. One worker only — scrape job
# state lives in memory and the film cache is a single lock-guarded file.
ENV PORT=8000
EXPOSE 8000
CMD ["python", "server.py"]
