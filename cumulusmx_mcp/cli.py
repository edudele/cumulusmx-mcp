"""Arranque del servidor: comprobación de conectividad con CumulusMX y servidor HTTP (o stdio)."""
import argparse
import logging
import os
import sys

import httpx

from . import __version__, client, config, prompts, resources, tools  # noqa: F401  (registran tools, prompts y recursos)
from .app import build_http_app, mcp
from .config import logger


def check_cumulus_connection() -> tuple[bool, str]:
    """Comprueba la conectividad con CumulusMX al arrancar e informa en el log."""
    auth = client._get_auth()
    base = config.BASE
    try:
        with httpx.Client(timeout=5.0) as http:
            resp = http.get(f"{base}/api/info/version.json", auth=auth)
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    ver = data.get("Version", "desconocida")
                    bld = data.get("Build", "")
                    return True, f"CumulusMX v{ver} (Build {bld}) responde correctamente"
                except Exception:
                    return True, "CumulusMX responde correctamente (HTTP 200)"
            elif resp.status_code in (401, 403):
                return False, f"HTTP {resp.status_code}: Error de autenticacion (revisa CUMULUS_USER / CUMULUS_PASSWORD)"
            else:
                return False, f"HTTP {resp.status_code} al consultar version"
    except httpx.ConnectError:
        return False, f"No se pudo conectar a {base} (Connection refused o host no alcanzable)"
    except httpx.TimeoutException:
        return False, f"Tiempo de espera agotado al conectar a {base} (timeout > 5s)"
    except Exception as exc:
        return False, f"Error de conexion: {exc}"


def setup_logging() -> None:
    """Configura el log (siempre a stderr: en modo stdio stdout es el canal del protocolo MCP)."""
    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stderr,
    )
    # Silencia los tracebacks excesivos de validacion interna de FastMCP
    for name in ("fastmcp", "mcp", "FastMCP"):
        logging.getLogger(name).setLevel(logging.WARNING)


def _log_startup(transport: str, host: str, port: int) -> None:
    user = os.environ.get("CUMULUS_USER")
    logger.info("=" * 60)
    where = f"http://{host}:{port}" if transport == "http" else "stdio"
    logger.info("Iniciando cumulusmx-mcp v%s en %s", __version__, where)
    logger.info("URL backend CumulusMX: %s", config.BASE)
    logger.info("Herramientas de escritura (ENABLE_WRITE_TOOLS): %s",
                "publicadas" if config.WRITE_TOOLS_ENABLED else "ocultas (modo solo lectura)")
    logger.info("Timeout/reintentos hacia CumulusMX: %ss / %d", config.CUMULUS_TIMEOUT, config.CUMULUS_RETRIES)
    logger.info("Basic Auth hacia CumulusMX: %s", f"configurada (usuario: {user})" if user else "desactivada")
    if transport == "http":
        logger.info("Bearer Auth para clientes MCP: %s%s", "activada" if config.TOKEN else "desactivada",
                    " (+ token de solo lectura)" if config.TOKEN_READONLY else "")
        logger.info("Prefijo strip (MCP_STRIP_PREFIX): %s", config.STRIP_PREFIX or "(ninguno)")
        logger.info("Endpoint de salud (sin auth): http://%s:%s/health", host, port)
    logger.info("Auditoría de escrituras: log%s", f" + {config.AUDIT_FILE}" if config.AUDIT_FILE else "")
    logger.info("Copias previas a escrituras: %s", config.BACKUP_DIR or "desactivadas")
    logger.info("Nivel de log (LOG_LEVEL): %s", config.LOG_LEVEL)
    ok_cumulus, cumulus_msg = check_cumulus_connection()
    if ok_cumulus:
        logger.info("✓ Comprobacion CumulusMX: %s", cumulus_msg)
    else:
        logger.warning("⚠ Comprobacion CumulusMX: %s", cumulus_msg)
    logger.info("=" * 60)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cumulusmx-mcp",
        description="Servidor MCP para estaciones meteorológicas CumulusMX. Configuración por variables de entorno (ver README).",
    )
    parser.add_argument("--transport", choices=("http", "stdio"), default="http",
                        help="http (por defecto, con autenticación Bearer) o stdio (cliente MCP local, p. ej. Claude Desktop)")
    parser.add_argument("--host", default="0.0.0.0", help="dirección de escucha en modo http (por defecto 0.0.0.0)")
    parser.add_argument("--port", type=int, default=None, help="puerto en modo http (por defecto $PORT o 8000)")
    parser.add_argument("--version", action="version", version=f"cumulusmx-mcp {__version__}")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    setup_logging()
    port = args.port if args.port is not None else config.PORT
    _log_startup(args.transport, args.host, port)
    if args.transport == "stdio":
        mcp.run(transport="stdio")
        return
    import uvicorn

    uvicorn.run(build_http_app(), host=args.host, port=port, log_level=config.LOG_LEVEL)


if __name__ == "__main__":
    main()
