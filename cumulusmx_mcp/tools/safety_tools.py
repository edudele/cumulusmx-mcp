"""Tools de seguridad: copias previas, restauración y registro de auditoría de escrituras."""
import json
from typing import Literal

from .. import backups, capture, config, safety, settings_engine, utils
from ..app import READ_ONLY, mcp, write_tool
from . import editors


@mcp.tool(annotations=READ_ONLY)
async def list_backups(kind: Literal["", "settings", "dayfile", "datalog", "recent", "mysqlcache", "records"] = "", limit: int = 20) -> str:
    """Copias de seguridad creadas antes de cada escritura (ajustes, líneas del dayfile y de los logs, datos recientes, caché MySQL, récords), de la más reciente a la
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


def _plan(record: dict) -> tuple[str, dict, object] | tuple[str, None, str]:
    """Qué hacer para restaurar una copia: (descripción, argumentos, corrutina-fábrica) o (tipo, None, motivo si no se puede)."""
    kind, target, content = record["tipo"], record["objetivo"], record["contenido"]
    deleted = isinstance(content, dict) and content.get("accion") == "Delete"
    if kind == "settings":
        return "update_settings(replace)", {"section": target}, lambda dry: settings_engine._settings_update(
            target, content, dry_run=dry, replace=True)
    if deleted:
        return kind, None, ("La copia es de un BORRADO: CumulusMX no tiene acción para reinsertar filas; el contenido guardado "
                            "(get_backup) sirve para recrearlas a mano.")
    if kind == "dayfile" and content.get("row"):
        line, values = content["line"], [str(v) for v in content["row"][1:]]
        return "edit_dayfile", {"action": "Edit", "line": line, "values": values}, lambda dry: editors._edit_dayfile(
            "Edit", line, values)
    if kind == "datalog" and content.get("row"):
        values = [str(v) for v in content["row"][1:]]
        while values and values[-1] == "-":      # CumulusMX rellena con "-" las columnas que la línea original no tenía
            values.pop()
        line, extra = content["line"], content["extra"]
        return "edit_datalog", {"action": "Edit", "line": line, "extra": extra, "values": values}, \
            lambda dry: editors._edit_datalog("Edit", line, values, extra)
    if kind == "recent" and content.get("row"):
        values = [str(v) for v in content["row"]]
        return "edit_recent_data", {"action": "Edit", "values": values}, lambda dry: editors._edit_recent("Edit", values)
    if kind == "mysqlcache" and len(content.get("items", [])) == 1:
        item = content["items"][0]
        return "edit_mysql_cache", {"action": "Edit", "keys": [item["key"]], "statements": [item["statement"]]}, \
            lambda dry: editors._edit_mysql_cache("Edit", [item["key"]], [item["statement"]])
    if kind == "records" and "valor" in content:
        args = {"scope": content["scope"], "field": content["field"], "month": content.get("month"),
                "value": capture.parse_regional_number(content["valor"]), "time": content["hora"]}
        return "edit_records", args, lambda dry: editors._edit_records(args["scope"], args["field"], args["value"],
                                                                       args["time"], args["month"])
    return kind, None, "Esta copia no contiene datos suficientes para restaurarla automáticamente (consulta get_backup)."


@write_tool
async def restore_backup(backup_id: str, dry_run: bool = True) -> str:
    """Deshace una escritura restaurando su copia previa. Restaura copias de ajustes (se reemplaza la sección), de líneas del
    dayfile y de los logs de intervalo, de registros de datos recientes, de sentencias de la caché MySQL y de récords
    (valor y hora anteriores), siempre que la escritura original fuera una EDICIÓN. Un borrado no se puede revertir porque
    CumulusMX no reinserta filas: la copia conserva su contenido (get_backup). dry_run=True (por defecto) muestra qué se
    haría sin aplicarlo. La restauración crea a su vez una copia del estado actual, así que también se puede deshacer."""
    record = backups.load(backup_id)
    action, args, runner = _plan(record)
    if args is None:
        return json.dumps({"aplicado": False, "tipo": action, "error": runner}, ensure_ascii=False)
    if dry_run:
        if record["tipo"] == "settings":
            return await runner(True)
        return json.dumps({"aplicado": False, "nota": "Vista previa: no se ha enviado nada a CumulusMX.",
                           "accion": {"herramienta": action, **args}}, ensure_ascii=False)
    return await runner(False)


@mcp.tool(annotations=READ_ONLY)
async def get_audit_log(limit: int = 50, tool: str = "") -> str:
    """Últimas escrituras realizadas a través de este servidor (herramienta, ámbito del token, argumentos sin secretos y
    resultado). Se conserva en memoria desde el arranque; con CUMULUS_AUDIT_FILE se guarda además en un fichero JSON Lines.
    Filtra por herramienta con `tool`."""
    return json.dumps({"fichero": config.AUDIT_FILE or None, "registros": safety.recent_audit(limit, tool)}, ensure_ascii=False)
