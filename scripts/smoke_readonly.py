#!/usr/bin/env python3
"""Prueba de humo SOLO DE LECTURA contra un servidor cumulusmx-mcp ya desplegado.

Uso:
    python scripts/smoke_readonly.py https://tu-dominio/mcp-res/mcp TU_TOKEN
    (o con las variables MCP_URL y MCP_TOKEN)

Llama a una selección de tools de lectura con argumentos seguros y muestra OK/FALLO. No ejecuta ninguna tool de escritura.
Sale con código 1 si alguna falla. Requiere `pip install fastmcp`.
"""
import asyncio
import json
import os
import sys

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

CHECKS: list[tuple[str, dict]] = [
    ("ping", {}),
    ("server_status", {}),
    ("current_data", {}),
    ("snapshot", {}),
    ("get_tags", {"tags": ["temp", "hum"]}),
    ("today_all", {}),
    ("station_info", {"kind": "version"}),
    ("station_info", {"kind": "units"}),
    ("graph_summary", {"name": "tempdata"}),
    ("extra_sensors", {"sensor": "temp"}),
    ("records_all", {"scope": "alltime"}),
    ("data_quality", {"max_items": 5}),
    ("climatology", {}),
    ("frost_dates", {}),
    ("wind_rose", {}),
    ("daily_briefing", {}),
    ("weather_alerts", {}),
    ("period_report", {"period": "week"}),
    ("get_settings", {"section": "mysql"}),
    ("get_settings", {"section": "mqtt"}),
    ("get_settings", {"section": "display"}),
    ("interval_data", {"start": "0", "end": "1", "fields": ["Temperature"]}),
    ("live_sample", {"seconds": 5, "max_messages": 1}),
    ("list_backups", {}),
    ("get_audit_log", {"limit": 3}),
]


def verdict(name: str, text: str) -> tuple[bool, str]:
    if text.startswith(("ERROR", "Error")):
        return False, text[:100]
    if name == "live_sample":
        data = json.loads(text)
        return ("error" not in data and data["n_mensajes"] >= 1), f"{data.get('n_mensajes')} mensajes"
    return True, f"{len(text)} caracteres"


async def main(url: str, token: str) -> int:
    transport = StreamableHttpTransport(url, headers={"Authorization": f"Bearer {token}"})
    failures = 0
    async with Client(transport) as client:
        tools = {t.name for t in await client.list_tools()}
        print(f"Conectado: {len(tools)} tools publicadas")
        for name, args in CHECKS:
            if name not in tools:
                print(f"  SALTADA  {name}: no está publicada en este servidor")
                continue
            try:
                result = await client.call_tool(name, args)
                ok, detail = verdict(name, result.content[0].text)
            except Exception as exc:  # noqa: BLE001 - queremos informar de cualquier fallo
                ok, detail = False, f"{type(exc).__name__}: {str(exc)[:100]}"
            failures += not ok
            print(f"  {'OK    ' if ok else 'FALLO '}  {name} {json.dumps(args) if args else ''} -> {detail}")
    print(f"\n{len(CHECKS) - failures} correctas, {failures} con fallo")
    return 1 if failures else 0


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("MCP_URL", "")
    secret = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("MCP_TOKEN", "")
    if not target or not secret:
        sys.exit(__doc__)
    sys.exit(asyncio.run(main(target, secret)))
