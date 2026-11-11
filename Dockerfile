FROM python:3.13-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    COURSES_ROOT=/app/courses \
    DATABASE_PATH=/app/data/cursos.sqlite3
# LibreOffice headless converts Office documents to PDF; keeps NAS files read-only.
# Fonts preserve common Word/PowerPoint layouts; CJK fonts cover Japanese lessons.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-writer libreoffice-calc libreoffice-impress \
    fonts-liberation fonts-dejavu-core fonts-noto-cjk libarchive13 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd -g 1000 cursos \
    && useradd -u 1000 -g 1000 -m -s /usr/sbin/nologin cursos
COPY . .
RUN mkdir -p /app/data /app/courses && chown -R 1000:1000 /app/data /app
USER 1000:1000
EXPOSE 5000
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/health',timeout=5)"
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "4", "--timeout", "180", "--access-logfile", "-", "offlineu_core:app"]
