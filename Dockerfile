FROM python:3.15-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

# 1) Dependencias fijadas (capa cacheable: solo cambia cuando cambia requirements.txt)
COPY requirements.txt .
RUN pip install -r requirements.txt

# 2) El paquete cumulusmx_mcp (sin volver a resolver dependencias) + el shim server.py
COPY pyproject.toml README.md server.py ./
COPY cumulusmx_mcp ./cumulusmx_mcp
RUN pip install --no-deps .

# Usuario sin privilegios y volumen para copias de seguridad y auditoría (CUMULUS_BACKUP_DIR / CUMULUS_AUDIT_FILE)
RUN useradd -r -u 10001 mcp && mkdir -p /data && chown mcp /data
USER mcp
VOLUME /data
ENV CUMULUS_BACKUP_DIR=/data/backups CUMULUS_AUDIT_FILE=/data/audit.jsonl

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD python3 -c "import os, urllib.request; port = os.environ.get('PORT', '8000'); urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=4)" || exit 1
CMD ["cumulusmx-mcp"]
