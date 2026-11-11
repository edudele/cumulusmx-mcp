"""Tools de seguridad: copias previas, restauración y registro de auditoría de escrituras."""
import json
from typing import Literal

from .. import backups, config, safety, settings_engine, utils
from ..app import READ_ONLY, mcp, write_tool
from . import editors


@mcp.tool(annotations=READ_ONLY)
async def list_backups(kind: Literal["", "settings", "dayfile", "records"] = "", limit: int = 20) -> str:
    """Copias de seguridad creadas antes de cada escritura (ajustes, líneas del dayfile, récords), de la más reciente a la
    más antigua. Solo metadatos: id, fecha, tipo, objetivo y herramienta. Usa get_backup para ver el contenido y
    restore_backup para deshacer. Si las copias están desactivadas (CUMULUS_BACKUP_DIR=none) devuelve una lista vacía."""
    return json.dumps({"activadas": backups.enabled(), "directorio": config.BACKUP_DIR or None,
                       "copias": backups.recent(kind, limit)}, ensure_ascii=False)


@mcp.tool(annotations=READ_ONLY)
async def get_backup(backup_id: str) -> str:
    """Contenido completo de una copia. Puede incluir contraseñas (se enmascaran con CUMULUS_MASK_SECRETS o con el token
    de solo lectura)."""
    record = backups.load(backup_id)
    if config.CUMULUS_MASK_SECRETS or safety.current_scope() == "readonly":
        record = utils._mask_secrets_in(record)
    return json.dumps(record, ensure_ascii=False)


@write_tool
async def restore_backup(backup_id: str, dry_run: bool = True) -> str:
    """Deshace una escritura restaurando su copia previa. Soporta copias de ajustes (se reemplaza la sección por la copia)
    y de líneas del dayfile (se reescribe la línea original). Las copias de récords son solo informativas: restáuralos
    con edit_records. dry_run=True (por defecto) muestra qué se haría sin aplicarlo. La restauración crea a su vez una
    copia del estado actual, así que también se puede deshacer."""
    record = backups.load(backup_id)
    kind, target, content = record["tipo"], record["objetivo"], record["contenido"]
    if kind == "settings":
        return await settings_engine._settings_update(target, content, dry_run=dry_run, replace=True)
    if kind == "dayfile":
        line, row = content["line"], content["row"]
        values = [str(v) for v in row[1:]]
        if dry_run:
            return json.dumps({"aplicado": False, "nota": "Vista previa: no se ha enviado nada a CumulusMX.",
                               "accion": {"herramienta": "edit_dayfile", "action": "Edit", "line": line, "values": values}},
                              ensure_ascii=False)
        return await editors._edit_dayfile("Edit", line, values)
    tool = "edit_records" if kind == "records" else "la tool de edición correspondiente"
    return json.dumps({"aplicado": False, "error": f"La restauración automática no está disponible para copias de tipo "
                       f"'{kind}'. Consulta el contenido con get_backup y corrige con {tool}."}, ensure_ascii=False)


@mcp.tool(annotations=READ_ONLY)
async def get_audit_log(limit: int = 50, tool: str = "") -> str:
    """Últimas escrituras realizadas a través de este servidor (herramienta, ámbito del token, argumentos sin secretos y
    resultado). Se conserva en memoria desde el arranque; con CUMULUS_AUDIT_FILE se guarda además en un fichero JSON Lines.
    Filtra por herramienta con `tool`."""
    return json.dumps({"fichero": config.AUDIT_FILE or None, "registros": safety.recent_audit(limit, tool)}, ensure_ascii=False)
