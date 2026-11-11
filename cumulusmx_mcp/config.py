"""Configuración del servidor: variables de entorno y constantes.

Todo se lee aquí, una sola vez al importar. El resto del código accede como `config.X` (nunca con `from .config import X`)
para que los tests puedan parchear valores con `patch.object(config, "X", ...)`.
"""
import logging
import os
import tempfile

logger = logging.getLogger("cumulusmx-mcp")
audit_logger = logging.getLogger("cumulusmx-mcp.audit")


def env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in ("false", "0", "no", "off", "")


LOG_LEVEL = os.environ.get("LOG_LEVEL", "info").lower()

# ---- servidor MCP
TOKEN = os.environ.get("MCP_AUTH_TOKEN", "").encode()
# Token opcional de solo lectura: permite consultar pero no ejecutar tools de escritura y enmascara los secretos.
TOKEN_READONLY = os.environ.get("MCP_AUTH_TOKEN_READONLY", "").encode()
PORT = int(os.environ.get("PORT", "8000"))
# GET /metrics (formato Prometheus, requiere el mismo token Bearer). MCP_METRICS=false lo desactiva.
METRICS_ENABLED = env_bool("MCP_METRICS", True)
# Prefijo externo opcional (p.ej. detrás de un proxy que no reescribe bien la ruta, como Pangolin con "clip prefix").
# Si llega /mcp-res/mcp lo tratamos como /mcp. Configurable por si el prefijo cambia; vacío = desactivado.
STRIP_PREFIX = os.environ.get("MCP_STRIP_PREFIX", "/mcp-res")

# ---- CumulusMX
BASE = os.environ.get("CUMULUSMX_URL", "http://cumulusmx:8998").rstrip("/")
# Peticiones GET: timeout por intento y reintentos ante fallos transitorios (conexión, timeout, 502/504)
CUMULUS_TIMEOUT = float(os.environ.get("CUMULUS_TIMEOUT", "30"))
CUMULUS_RETRIES = max(0, int(os.environ.get("CUMULUS_RETRIES", "2")))
RETRY_STATUS = frozenset({502, 504})
RETRY_BACKOFF_S = 0.3
MAX_CHARS = 60000  # recorte de respuestas de texto
# Zona IANA de la estación (necesaria para fechas legibles en interval_data/daily_data)
CUMULUS_TIMEZONE = os.environ.get("CUMULUS_TIMEZONE", "").strip()

# ---- histórico
DATA_START_YEAR = int(os.environ.get("CUMULUS_DATA_START_YEAR", "2023"))
FROST_THRESHOLD = float(os.environ.get("CUMULUS_FROST_THRESHOLD", "0.0"))
WARM_THRESHOLDS = (25.0, 30.0)
CURRENT_YEAR_TTL_S = int(os.environ.get("CUMULUS_CACHE_TTL", "600"))

# ---- escrituras y seguridad
WRITE_DISABLED_MSG = (
    "Error: Las operaciones de modificación/escritura están deshabilitadas en la configuración (ENABLE_WRITE_TOOLS=false)."
)
WRITE_TOOLS_ENABLED = os.environ.get("ENABLE_WRITE_TOOLS", "true").strip().lower() not in ("false", "0", "no", "off")
CUMULUS_MASK_SECRETS = env_bool("CUMULUS_MASK_SECRETS", False)

# Auditoría de escrituras: siempre al log; además a un fichero JSON Lines si se indica CUMULUS_AUDIT_FILE
AUDIT_FILE = os.environ.get("CUMULUS_AUDIT_FILE", "").strip()
# Copias previas a cada escritura. CUMULUS_BACKUP_DIR=none (o vacío) las desactiva; por defecto, directorio temporal
# (monta un volumen para conservarlas entre reinicios).
_backup_env = os.environ.get("CUMULUS_BACKUP_DIR")
BACKUP_DIR = (
    os.path.join(tempfile.gettempdir(), "cumulusmx-mcp-backups")
    if _backup_env is None
    else ("" if _backup_env.strip().lower() in ("", "none", "off", "false", "0") else _backup_env.strip())
)
BACKUP_KEEP = max(1, int(os.environ.get("CUMULUS_BACKUP_KEEP", "200")))  # nº máximo de copias conservadas
