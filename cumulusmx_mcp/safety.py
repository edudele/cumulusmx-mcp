"""Seguridad de las escrituras: interruptor global, ámbito del token y auditoría.

- `_check_write_enabled()` es la única guarda: deniega si ENABLE_WRITE_TOOLS=false o si la petición llegó con el token de
  solo lectura (MCP_AUTH_TOKEN_READONLY).
- `audit()` registra cada escritura (herramienta, argumentos sin secretos, resultado) en el log y, opcionalmente, en un
  fichero JSON Lines (CUMULUS_AUDIT_FILE). Guarda además los últimos registros en memoria para `get_audit_log`.
"""
import collections
import datetime
import hmac
import json
import os
from typing import Any

from . import config, utils

READONLY_MSG = "Error: este token es de solo lectura; las operaciones de escritura no están permitidas con él."
_RECENT: collections.deque = collections.deque(maxlen=500)


def bearer_token_from_request() -> bytes | None:
    """Token Bearer de la petición HTTP en curso, o None si no hay petición HTTP (stdio, pruebas...)."""
    try:
        from fastmcp.server.dependencies import get_http_headers
    except ImportError:  # pragma: no cover - versiones de FastMCP sin esta utilidad
        return None
    try:
        headers = get_http_headers(include={"authorization"})
    except Exception:  # pragma: no cover
        return None
    auth = headers.get("authorization", "")
    return auth[7:].encode() if auth.startswith("Bearer ") else None


def current_scope() -> str:
    """'full' o 'readonly'. Sin petición HTTP (stdio) o con el token principal es 'full'; un token desconocido, 'readonly'."""
    token = bearer_token_from_request()
    if token is None:
        return "full"
    if config.TOKEN and hmac.compare_digest(token, config.TOKEN):
        return "full"
    return "readonly"


def _check_write_enabled() -> str | None:
    """None si se puede escribir; si no, el mensaje de error que debe devolver la tool."""
    val = os.environ.get("ENABLE_WRITE_TOOLS", "true").strip().lower()
    if val in ("false", "0", "no", "off"):
        return config.WRITE_DISABLED_MSG
    if current_scope() == "readonly":
        return READONLY_MSG
    return None


def result_failed(result: Any) -> bool:
    """¿El texto devuelto por una tool de escritura es un error de CumulusMX? Las tools devuelven los errores HTTP y de
    conexión como texto ("ERROR HTTP 500 en ...") en vez de lanzar excepciones, y la auditoría debe distinguirlos."""
    if not isinstance(result, str):
        return False
    text = result.lstrip()
    if text.startswith("ERROR"):
        return True
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except ValueError:
            return False
        if isinstance(data, dict):
            inner = data.get("respuesta")
            return bool((isinstance(inner, str) and inner.lstrip().startswith("ERROR")) or data.get("error"))
    return False


def _shorten(value: Any, limit: int = 400) -> Any:
    if isinstance(value, str) and value.lstrip().startswith(("{", "[")):
        try:  # JSON en texto (p. ej. settings="{...}"): enmascarar sus secretos
            value = utils._mask_secrets_in(json.loads(value))
        except ValueError:
            pass
    if isinstance(value, (dict, list)):
        text = json.dumps(utils._mask_secrets_in(value), ensure_ascii=False, default=str)
    else:
        text = value if isinstance(value, str) else repr(value)
    return text if len(text) <= limit else f"{text[:limit]}…[{len(text)} caracteres]"


def audit(tool: str, arguments: dict[str, Any], outcome: str, detail: Any = "") -> dict[str, Any]:
    """Registra una escritura. `outcome`: ok | error | denegado. Nunca lanza excepciones."""
    record = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "herramienta": tool,
        "ambito": current_scope(),
        "resultado": outcome,
        "argumentos": {k: _shorten(v) for k, v in arguments.items()},
        "detalle": _shorten(detail, 300),
    }
    try:
        line = json.dumps(record, ensure_ascii=False)
        config.audit_logger.info("AUDIT %s", line)
        _RECENT.append(record)
        if config.AUDIT_FILE:
            os.makedirs(os.path.dirname(os.path.abspath(config.AUDIT_FILE)), exist_ok=True)
            with open(config.AUDIT_FILE, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    except Exception as exc:  # la auditoría no debe romper la operación
        config.logger.error("No se pudo registrar la auditoría: %s", exc)
    return record


def recent_audit(limit: int = 50, tool: str = "") -> list[dict[str, Any]]:
    """Últimos registros de auditoría de esta sesión del servidor (más recientes primero)."""
    items = [r for r in reversed(_RECENT) if not tool or r["herramienta"] == tool]
    return items[: max(1, min(limit, 500))]
