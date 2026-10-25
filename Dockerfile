# Small runtime image: `docker build -t price-monitor .`
# Run:  docker run --rm -v "$PWD/data:/app/data" -v "$PWD/reports:/app/reports" price-monitor run
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install . && useradd --create-home --uid 1000 monitor && mkdir -p data reports \
    && chown monitor:monitor data reports

USER monitor
VOLUME ["/app/data", "/app/reports"]
ENTRYPOINT ["price-monitor"]
CMD ["run"]
