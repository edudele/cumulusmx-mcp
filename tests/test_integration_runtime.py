"""Integración real: arranca el servidor como proceso (HTTP y stdio) y lo usa con un cliente MCP.

Verifica lo que los mocks no pueden: que el ámbito del token se aplica con cabeceras HTTP reales, que el transporte stdio
funciona y que el arranque no hace llamadas de red inesperadas."""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport, StreamableHttpTransport

ROOT = Path(__file__).resolve().parent.parent
ENV = {**os.environ, "PYTHONPATH": str(ROOT), "MCP_AUTH_TOKEN": "full-tok", "MCP_AUTH_TOKEN_READONLY": "ro-tok",
       "CUMULUSMX_URL": "http://127.0.0.1:9", "CUMULUS_RETRIES": "0", "CUMULUS_BACKUP_DIR": "none", "MCP_STRIP_PREFIX": ""}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server_url(tmp_path_factory):
    port = free_port()
    audit = tmp_path_factory.mktemp("audit") / "audit.jsonl"
    proc = subprocess.Popen([sys.executable, "-m", "cumulusmx_mcp", "--host", "127.0.0.1", "--port", str(port)],
                            env={**ENV, "CUMULUS_AUDIT_FILE": str(audit)}, cwd=ROOT,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            urllib.request.urlopen(f"{base}/health", timeout=1)
            break
        except Exception:
            time.sleep(0.2)
    else:
        proc.kill()
        pytest.fail(f"el servidor no arrancó: {proc.stderr.read()[-800:]}")
    yield base, audit
    proc.terminate()
    proc.wait(timeout=15)


def transport(base, token):
    return StreamableHttpTransport(f"{base}/mcp", headers={"Authorization": f"Bearer {token}"})


@pytest.mark.asyncio
async def test_full_token_lists_and_runs_tools(server_url):
    base, _ = server_url
    async with Client(transport(base, "full-tok")) as c:
        tools = await c.list_tools()
        assert len(tools) >= 89 and (await c.call_tool("ping", {})).content[0].text == "pong"


@pytest.mark.asyncio
async def test_readonly_token_can_read_but_writes_are_denied_and_audited(server_url):
    base, audit = server_url
    async with Client(transport(base, "ro-tok")) as c:
        assert (await c.call_tool("ping", {})).content[0].text == "pong"
        denied = await c.call_tool("mysql_table", {"action": "create", "table": "monthly"}, raise_on_error=False)
        assert "solo lectura" in denied.content[0].text
        log = json.loads((await c.call_tool("get_audit_log", {"tool": "mysql_table"})).content[0].text)
        assert log["registros"][0]["ambito"] == "readonly" and log["registros"][0]["resultado"] == "denegado"
    async with Client(transport(base, "full-tok")) as c:     # el token completo sí llega a intentarlo (la estación está caída)
        out = await c.call_tool("mysql_table", {"action": "create", "table": "monthly"}, raise_on_error=False)
        assert "ERROR de conexión" in out.content[0].text
    lines = [json.loads(line) for line in audit.read_text().splitlines()]
    assert [(r["ambito"], r["resultado"]) for r in lines if r["herramienta"] == "mysql_table"] == [("readonly", "denegado"),
                                                                                                    ("full", "fallo")]


def test_invalid_or_missing_token_is_rejected(server_url):
    base, _ = server_url
    for headers in ({}, {"Authorization": "Bearer nope"}, {"Authorization": "Basic Zm9vOmJhcg=="}):
        req = urllib.request.Request(f"{base}/mcp", method="POST", headers=headers, data=b"{}")
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req, timeout=5)
        assert exc.value.code == 401
    assert json.loads(urllib.request.urlopen(f"{base}/health", timeout=5).read())["status"] == "healthy"


@pytest.mark.asyncio
async def test_stdio_transport_works_with_full_scope_and_no_banner_on_stdout():
    st = StdioTransport(command=sys.executable, args=["-m", "cumulusmx_mcp", "--transport", "stdio"], env=ENV, cwd=str(ROOT))
    async with Client(st) as c:
        assert len(await c.list_tools()) >= 89
        assert (await c.call_tool("ping", {})).content[0].text == "pong"
        out = await c.call_tool("mysql_table", {"action": "create", "table": "monthly"}, raise_on_error=False)
        assert "solo lectura" not in out.content[0].text          # sin petición HTTP el ámbito es completo


def test_startup_does_not_check_pypi_for_updates():
    code = ("import os, cumulusmx_mcp; from fastmcp.settings import Settings; "
            "print(os.environ['FASTMCP_CHECK_FOR_UPDATES'], Settings().check_for_updates)")
    env = {k: v for k, v in ENV.items() if k != "FASTMCP_CHECK_FOR_UPDATES"}
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, cwd=ROOT, timeout=60)
    assert out.stdout.split() == ["off", "off"], out.stderr
