"""Pruebas de integración contra un CumulusMX REAL (estación Simulator, datos de prueba sembrados por tests_integration/seed.py).

Variables de entorno (las prepara el workflow .github/workflows/integration.yml):
  CUMULUSMX_REAL_URL   URL de la API, p. ej. http://127.0.0.1:8998
  CUMULUSMX_DATA_DIR   carpeta `data` de ese CumulusMX (para comparar los ficheros reales byte a byte)
Sin ellas, todas estas pruebas se omiten (no forman parte de `pytest` a secas).
"""
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

REAL_URL = os.environ.get("CUMULUSMX_REAL_URL", "")
DATA_DIR = Path(os.environ.get("CUMULUSMX_DATA_DIR", "")) if os.environ.get("CUMULUSMX_DATA_DIR") else None

if REAL_URL:
    # Deben fijarse ANTES de importar el paquete (config lee el entorno una sola vez)
    _tmp = Path(tempfile.mkdtemp(prefix="mcp-it-"))
    os.environ.update({
        "CUMULUSMX_URL": REAL_URL, "CUMULUS_TIMEZONE": "UTC", "CUMULUS_RETRIES": "0", "CUMULUS_TIMEOUT": "60",
        "CUMULUS_BACKUP_DIR": str(_tmp / "backups"), "CUMULUS_AUDIT_FILE": str(_tmp / "audit.jsonl"),
        "CUMULUS_DATA_START_YEAR": "2025", "ENABLE_WRITE_TOOLS": "true", "MCP_AUTH_TOKEN": "it-token",
    })
    os.environ.pop("CUMULUS_USER", None)
    from cumulusmx_mcp import _facade  # noqa: E402

    _facade.install("server")


def pytest_collection_modifyitems(config, items):
    if REAL_URL:
        return
    skip = pytest.mark.skip(reason="define CUMULUSMX_REAL_URL y CUMULUSMX_DATA_DIR (ver tests_integration/conftest.py)")
    for item in items:
        item.add_marker(skip)


@pytest.fixture(scope="session")
def data_dir() -> Path:
    assert DATA_DIR and DATA_DIR.is_dir(), f"CUMULUSMX_DATA_DIR no válido: {DATA_DIR}"
    return DATA_DIR


@pytest.fixture(scope="session")
def seed(data_dir) -> dict:
    return json.loads((data_dir.parent / "seed.json").read_text(encoding="utf-8"))
