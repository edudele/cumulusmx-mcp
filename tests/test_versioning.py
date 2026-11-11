"""Pruebas de versionado y de coherencia de la release.

Verifican que:
1. `__version__` es SemVer y coincide con la primera entrada de CHANGELOG.md
   (el tag `vX.Y.Z` que dispara el workflow de Docker debe corresponder a esa versión).
2. La versión se expone en server_status y en el servidor MCP.
3. `rankings` usa CUMULUS_DATA_START_YEAR cuando no se indica start_year.
4. La skill incluida en el repo tiene un frontmatter válido y referencia solo tools reales.
"""
import json
import os
import re
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))
os.environ.setdefault("MCP_AUTH_TOKEN", "test-secret-token-12345")

import server  # noqa: E402

SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
SKILL_DIR = PROJECT_DIR / "skills" / "cumulusmx-weather"


def test_version_is_semver():
    assert SEMVER_RE.match(server.__version__)


def test_changelog_matches_version():
    changelog = (PROJECT_DIR / "CHANGELOG.md").read_text(encoding="utf-8")
    versions = re.findall(r"^## \[(\d+\.\d+\.\d+)\]", changelog, flags=re.MULTILINE)
    assert versions, "CHANGELOG.md no tiene entradas '## [X.Y.Z]'"
    assert versions[0] == server.__version__, (
        f"CHANGELOG ({versions[0]}) y server.__version__ ({server.__version__}) no coinciden"
    )


def test_fastmcp_reports_version():
    assert server.mcp.version == server.__version__


@pytest.mark.asyncio
async def test_server_status_includes_mcp_version():
    with patch.object(server, "_get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = '{"Version": "4.0.0"}'
        res = json.loads(await server.server_status())
    assert res["mcp_version"] == server.__version__
    assert res["mcp"] == "cumulusmx-mcp"


@pytest.mark.asyncio
async def test_rankings_default_start_year_uses_env_setting():
    with patch.object(server, "DATA_START_YEAR", 2019), \
         patch.object(server, "_get_dayfile_rows", new_callable=AsyncMock) as mock_rows:
        mock_rows.return_value = []
        res = json.loads(await server.rankings(variable="temp_max"))
    assert res["periodo"]["start_year"] == 2019
    assert mock_rows.await_args.kwargs["start_year"] == 2019


from tests.compat import is_read_only as _is_read_only  # noqa: E402


def _skill_frontmatter() -> dict[str, str]:
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\n"), "el frontmatter debe empezar en la primera línea"
    block = text.split("\n---\n", 1)[0][4:]
    fm = {}
    for line in block.splitlines():
        if line.startswith((" ", "\t")):  # claves anidadas (p. ej. dentro de metadata)
            continue
        key, _, value = line.partition(":")
        fm[key.strip()] = value.strip()
    return fm


def test_skill_frontmatter_is_valid():
    fm = _skill_frontmatter()
    assert fm["name"] == "cumulusmx-weather" == SKILL_DIR.name
    assert re.fullmatch(r"[a-z0-9-]{1,64}", fm["name"])
    assert 0 < len(fm["description"]) <= 1024
    # Campos portables entre claude.ai / API / otros agentes
    assert set(fm) <= {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
    assert len((SKILL_DIR / "SKILL.md").read_text(encoding="utf-8").splitlines()) < 500


@pytest.mark.asyncio
async def test_skill_documents_every_tool_and_only_real_tools():
    registered = {t.name for t in await server.mcp.list_tools()}
    skill_text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    reference_text = (SKILL_DIR / "reference.md").read_text(encoding="utf-8")

    # Toda tool del servidor aparece en la referencia de la skill
    missing = {t for t in registered if f"`{t}`" not in reference_text}
    assert not missing, f"tools sin documentar en reference.md: {sorted(missing)}"

    # Toda tool citada en la primera columna de las tablas existe de verdad
    documented = set()
    for line in reference_text.splitlines():
        if line.startswith("| `"):
            documented |= set(re.findall(r"`([a-z_]+)`", line.split("|")[1]))
    assert documented <= registered, f"reference.md cita tools inexistentes: {sorted(documented - registered)}"

    # Las tools de escritura de SKILL.md coinciden con las destructivas reales
    destructive = {t.name for t in await server.mcp.list_tools() if not _is_read_only(t)}
    listed_as_write = {n for n in destructive if f"`{n}`" in skill_text}
    assert listed_as_write == destructive, f"SKILL.md no lista como escritura: {sorted(destructive - listed_as_write)}"


def test_skill_metadata_version_matches_server():
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    m = re.search(r'^\s+version:\s*"?(\d+\.\d+\.\d+)"?\s*$', text, flags=re.MULTILINE)
    assert m, "SKILL.md debe declarar metadata.version"
    assert m.group(1) == server.__version__
