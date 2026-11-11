"""Pruebas unitarias del almacén de copias."""
import os
import stat
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cumulusmx_mcp import backups, config  # noqa: E402


@pytest.fixture
def bdir(tmp_path):
    with patch.object(config, "BACKUP_DIR", str(tmp_path / "b")):
        yield tmp_path / "b"


def test_save_and_load_roundtrip_with_private_permissions(bdir):
    bid = backups.save("settings", "mysql", {"server": {"pass": "x"}}, "update_settings")
    rec = backups.load(bid)
    assert rec["tipo"] == "settings" and rec["objetivo"] == "mysql" and rec["contenido"] == {"server": {"pass": "x"}}
    assert rec["herramienta"] == "update_settings" and rec["id"] == bid
    assert stat.S_IMODE(os.stat(bdir / f"{bid}.json").st_mode) == 0o600
    assert stat.S_IMODE(os.stat(bdir).st_mode) == 0o700


def test_ids_are_unique_and_sorted_chronologically(bdir):
    ids = [backups.save("settings", "mysql", {"n": i}, "t") for i in range(5)]
    assert len(set(ids)) == 5 and ids == sorted(ids)
    assert [c["id"] for c in backups.recent()] == ids[::-1]


def test_listing_filters_and_limits(bdir):
    backups.save("settings", "mysql", 1, "t")
    backups.save("dayfile", "linea3", 2, "t")
    backups.save("settings", "mqtt", 3, "t")
    assert len(backups.recent(limit=2)) == 2
    assert {c["objetivo"] for c in backups.recent("settings")} == {"mysql", "mqtt"}
    assert backups.recent("records") == []


def test_prune_keeps_only_the_newest(bdir):
    with patch.object(config, "BACKUP_KEEP", 3):
        ids = [backups.save("settings", "x", i, "t") for i in range(6)]
    assert [c["id"] for c in backups.recent(limit=50)] == ids[-3:][::-1]


@pytest.mark.parametrize("bad", ["../etc/passwd", "a/b", "", "x" * 200, "a b", "..", "a\\b"])
def test_ids_are_validated_before_touching_the_filesystem(bdir, bad):
    with pytest.raises(ValueError):
        backups.load(bad)


def test_unknown_id(bdir):
    with pytest.raises(ValueError, match="No existe"):
        backups.load("20240101T000000000000Z-settings-mysql")


def test_disabled_backups(tmp_path):
    for off in ("", None):
        with patch.object(config, "BACKUP_DIR", off):
            assert backups.enabled() is False and backups.save("settings", "x", 1, "t") is None
            assert backups.recent() == []
            with pytest.raises(ValueError):
                backups.load("anything")


def test_corrupt_files_are_skipped_in_listings(bdir):
    backups.save("settings", "mysql", 1, "t")
    (bdir / "00000000T000000000000Z-corrupt.json").write_text("{not json")
    assert len(backups.recent()) == 1


def test_default_backup_dir_is_a_temp_directory():
    env = {k: v for k, v in os.environ.items() if k != "CUMULUS_BACKUP_DIR"}
    import importlib
    with patch.dict(os.environ, env, clear=True):
        reloaded = importlib.reload(config)
        assert reloaded.BACKUP_DIR.endswith("cumulusmx-mcp-backups")
    for off in ("none", "off", "", "0"):
        with patch.dict(os.environ, {"CUMULUS_BACKUP_DIR": off}):
            assert importlib.reload(config).BACKUP_DIR == ""
    with patch.dict(os.environ, {"CUMULUS_BACKUP_DIR": "/data/bk"}):
        assert importlib.reload(config).BACKUP_DIR == "/data/bk"
    importlib.reload(config)   # restaura el estado del entorno de pruebas
