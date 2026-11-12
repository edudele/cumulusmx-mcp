"""Pruebas de extremo a extremo contra el simulador de CumulusMX (tests/simulator.py).

A diferencia de los mocks, el simulador parsea los cuerpos como lo hace el servidor real (prefijo json=, UrlDecode,
cultura regional...), de modo que un error de formato se manifiesta como un valor corrupto en su estado interno."""
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import server  # noqa: E402
from cumulusmx_mcp import app as app_module  # noqa: E402
from cumulusmx_mcp import backups, config, safety  # noqa: E402
from tests.simulator import FakeCumulus, csharp_url_decode  # noqa: E402


@pytest.fixture
def backup_dir(tmp_path):
    with patch.object(config, "BACKUP_DIR", str(tmp_path / "backups")):
        yield tmp_path / "backups"


@pytest.fixture
def sim(backup_dir):
    cumulus = FakeCumulus(decimal_comma=True)
    with patch.object(server, "_cli", return_value=cumulus.client()):
        yield cumulus


def posts(sim, prefix=""):
    return [r for r in sim.requests if r[0] == "POST" and r[1].startswith(prefix)]


class TestSimulatorSanity:
    """Comprueba que el simulador reproduce los fallos reales que queremos poder detectar."""

    def test_csharp_urldecode_turns_plus_into_space(self):
        assert csharp_url_decode('{"p":"a+b"}') == '{"p":"a b"}'
        assert csharp_url_decode("%2B") == "+"

    @pytest.mark.asyncio
    async def test_unencoded_alarm_body_would_be_corrupted(self, sim):
        raw = json.dumps({"data": {}, "units": {}, "email": {"password": "a+b%20c"}})
        await server._post("/api/setsettings/updatealarmconfig.json", content=raw)      # lo que hacía la v1.5
        assert sim.settings["alarms.json"]["email"]["password"] == "a b c"                # corrupto


class TestSettingsEndToEnd:
    @pytest.mark.asyncio
    async def test_mysql_update_with_special_characters_is_exact(self, sim):
        pwd = "p+ss%20w&rd=é ñ"
        res = json.loads(await server.update_mysql_settings({"server": {"pass": pwd}, "monthly": {"enabled": True}}))
        state = sim.settings["mysqldata.json"]
        assert res["aplicado"] is True and state["server"]["pass"] == pwd and state["monthly"]["enabled"] is True
        assert state["server"]["host"] == "db" and state["monthly"]["table"] == "Monthly"   # el resto intacto

    @pytest.mark.asyncio
    async def test_alarm_password_survives_thanks_to_url_encoding(self, sim):
        await server.update_settings("alarms", {"email": {"password": "p+q%r"}})
        assert sim.settings["alarms.json"]["email"]["password"] == "p+q%r"
        await server.update_alarm_settings({"BatteryLow": {"enabled": True}})
        assert sim.settings["alarms.json"]["data"]["BatteryLow"]["enabled"] is True
        assert sim.settings["alarms.json"]["email"]["password"] == "p+q%r"

    @pytest.mark.asyncio
    async def test_legacy_station_updater_uses_the_safe_engine(self, sim):
        out = await server.update_station_settings({"AnnualRainfall": {"ytdamount": 75.0}})
        assert out == "success"
        assert sim.settings["stationdata.json"]["AnnualRainfall"] == {"ytdamount": 75.0, "rainseasonstart": 1}

    @pytest.mark.asyncio
    async def test_no_blind_write_when_the_section_cannot_be_read(self, sim):
        with pytest.raises(ValueError):
            await server.update_settings("program", {"x": 1})          # el simulador no tiene programdata -> 404
        with pytest.raises(ValueError):
            await server.update_station_settings.__wrapped__({"x": 1}) if False else await server.update_settings("internet", {"x": 1})
        assert posts(sim) == []

    @pytest.mark.asyncio
    async def test_tags_use_plain_numbers_when_asked(self, sim):
        assert json.loads(await server.get_tags(["temp"]))["temp"] == "10.0"
        assert json.loads(await server.get_tags(["temp"], plain_numbers=False))["temp"] == "10,0"

    @pytest.mark.asyncio
    async def test_mysql_table_hits_the_official_endpoint(self, sim):
        await server.mysql_table("create", "dayfile")
        await server.mysql_table("update", "realtime")
        assert sim.created_tables == ["createdayfilesql.json", "updaterealtimesql.json"]


class TestBackupsAndRestore:
    @pytest.mark.asyncio
    async def test_settings_update_saves_the_previous_state_and_can_be_undone(self, sim):
        original = json.loads(json.dumps(sim.settings["mysqldata.json"]))
        res = json.loads(await server.update_mysql_settings({"server": {"host": "otro"}, "monthly": {"enabled": True}}))
        assert res["copia_id"] and backups.load(res["copia_id"])["contenido"] == original
        # vista previa
        prev = json.loads(await server.restore_backup(res["copia_id"]))
        assert prev["aplicado"] is False and sim.settings["mysqldata.json"]["server"]["host"] == "otro"
        # restaurar
        undone = json.loads(await server.restore_backup(res["copia_id"], dry_run=False))
        assert undone["aplicado"] is True and sim.settings["mysqldata.json"] == original
        # y la restauración también se puede deshacer
        assert undone["copia_id"]
        await server.restore_backup(undone["copia_id"], dry_run=False)
        assert sim.settings["mysqldata.json"]["server"]["host"] == "otro"

    @pytest.mark.asyncio
    async def test_dayfile_edit_saves_the_original_row_and_restores_it(self, sim):
        original = list(sim.dayfile[0])
        new_values = ["24/08/19", "99.0", "142", "15:04", "12.1"]
        res = json.loads(await server.edit_dayfile("Edit", 1, new_values))
        assert res["copia_id"] and sim.dayfile[0] == [1, *new_values]
        assert backups.load(res["copia_id"])["contenido"] == {"line": 1, "row": original, "accion": "Edit"}
        out = json.loads(await server.restore_backup(res["copia_id"], dry_run=False))
        assert sim.dayfile[0] == original and out["copia_id"]

    @pytest.mark.asyncio
    async def test_dayfile_edit_with_wrong_date_is_rejected_by_cumulusmx(self, sim):
        res = json.loads(await server.edit_dayfile("Edit", 1, ["25/08/19", "1"]))     # la línea 1 es del 24/08/19
        assert "ERROR HTTP 500" in res["respuesta"] and sim.dayfile[0][2] == "15.0"

    @pytest.mark.asyncio
    async def test_dayfile_dry_run_restore_describes_the_action(self, sim):
        res = json.loads(await server.edit_dayfile("Edit", 2, ["25/08/19", "1"]))
        prev = json.loads(await server.restore_backup(res["copia_id"]))
        assert prev["accion"] == {"herramienta": "edit_dayfile", "action": "Edit", "line": 2,
                                  "values": ["25/08/19", "19.0", "138", "14:20", "15.4"]}

    @pytest.mark.asyncio
    async def test_records_edit_saves_previous_value_and_restores_it(self, sim):
        res = json.loads(await server.edit_records("alltime", "highTemp", 35.5, "26/07/2019 16:00"))
        content = backups.load(res["copia_id"])["contenido"]
        assert content["valor"] == "34,6" and content["hora"] == "25/07/2019 15:39" and sim.records["alltime"]["highTemp"][0] == 35.5
        out = json.loads(await server.restore_backup(res["copia_id"], dry_run=False))
        assert out["copia_id"] and sim.records["alltime"]["highTemp"] == (34.6, "25/07/2019 15:39")

    @pytest.mark.asyncio
    async def test_monthly_records_backup_uses_the_month_prefix(self, sim):
        res = json.loads(await server.edit_records("monthly", "highTemp", 40.0, "11/07/2019 12:00", month=7))
        assert backups.load(res["copia_id"])["contenido"]["valor"] == "33,0"
        await server.restore_backup(res["copia_id"], dry_run=False)
        assert sim.records["monthly"]["7-highTemp"] == (33.0, "10/07/2019 15:00")

    @pytest.mark.asyncio
    async def test_datalog_edit_is_backed_up_and_restored(self, sim):
        original = list(sim.datalogs[False][1])
        res = json.loads(await server.edit_datalog("Edit", 2, ["24/08/19 10:10", "1566641400", "99,9", "62"]))
        assert sim.datalogs[False][1][3] == "99,9" and backups.load(res["copia_id"])["contenido"]["row"] == original
        await server.restore_backup(res["copia_id"], dry_run=False)
        assert sim.datalogs[False][1] == [2, "24/08/19 10:10", "1566641400", "10,7", "61"]

    @pytest.mark.asyncio
    async def test_recent_data_edit_is_backed_up_and_restored(self, sim):
        original = list(sim.recent[0])
        edited = list(original)
        edited[7] = 88.8                                                   # fila completa, con la fecha en texto delante
        res = json.loads(await server.edit_recent_data("Edit", edited))
        assert "ERROR" not in res["respuesta"] and sim.recent[0][7] == 88.8
        await server.restore_backup(res["copia_id"], dry_run=False)
        assert sim.recent[0] == original                                   # idéntico: fecha, nulos y decimales incluidos

    @pytest.mark.asyncio
    async def test_recent_data_edit_accepts_the_row_without_the_text_date(self, sim):
        original = list(sim.recent[1])
        edited = [*original[1:]]
        edited[6] = 77.7
        res = json.loads(await server.edit_recent_data("Edit", edited))
        assert "ERROR" not in res["respuesta"] and sim.recent[1][7] == 77.7 and sim.recent[1][0] == original[0]
        await server.restore_backup(res["copia_id"], dry_run=False)
        assert sim.recent[1] == original

    @pytest.mark.asyncio
    async def test_recent_data_edit_with_a_truncated_row_sends_nothing(self, sim):
        with pytest.raises(ValueError):
            await server.edit_recent_data("Edit", ["24/08/19 10:00", "1566640800", "88,8"])
        assert not posts(sim, "/api/edit/recentdata")

    @pytest.mark.asyncio
    async def test_rain_today_is_edited_and_restored(self, sim):
        res = json.loads(await server.edit_rain_today(4.5))
        assert sim.rain["raintoday"] == "4.5" and res["copia_id"]
        out = json.loads(await server.restore_backup(res["copia_id"], dry_run=False))
        assert out["aplicado"] is True and sim.rain == {"raintoday": "0.0", "midnightrain": "0.0", "rainratetoday": "0.0"}

    @pytest.mark.asyncio
    async def test_editor_restores_report_aplicado(self, sim):
        res = json.loads(await server.edit_dayfile("Edit", 1, ["24/08/19", "1"]))
        assert json.loads(await server.restore_backup(res["copia_id"], dry_run=False))["aplicado"] is True

    @pytest.mark.asyncio
    async def test_integer_records_are_sent_as_integers(self, sim):
        res = json.loads(await server.edit_records("alltime", "lowHumidity", 7, "15/09/2024 10:00"))
        assert "ERROR" not in res["respuesta"] and sim.records["alltime"]["lowHumidity"] == (7, "15/09/2024 10:00")
        assert "value=7&" in posts(sim, "/api/edit/alltime")[-1][2]
        await server.restore_backup(res["copia_id"], dry_run=False)
        assert sim.records["alltime"]["lowHumidity"] == (6, "14/09/2024 16:45")

    @pytest.mark.asyncio
    async def test_integer_records_reject_decimals_before_sending(self, sim):
        with pytest.raises(ValueError, match="entero"):
            await server.edit_records("alltime", "lowHumidity", 7.5, "15/09/2024 10:00")
        assert not posts(sim, "/api/edit/alltime")

    @pytest.mark.asyncio
    async def test_mysql_cache_edit_is_backed_up_and_restored(self, sim):
        res = json.loads(await server.edit_mysql_cache("Edit", [7], ["SELECT 1"]))
        assert sim.mysqlcache[0][1] == "SELECT 1"
        await server.restore_backup(res["copia_id"], dry_run=False)
        assert sim.mysqlcache[0] == [7, "INSERT INTO Monthly VALUES (1)"]

    @pytest.mark.asyncio
    async def test_mysql_cache_delete_reads_the_statements_itself(self, sim):
        res = json.loads(await server.edit_mysql_cache("Delete", [7]))
        assert [r[0] for r in sim.mysqlcache] == [8] and "ERROR" not in res["respuesta"]
        assert json.loads(await server.restore_backup(res["copia_id"], dry_run=False))["aplicado"] is False   # no reinsertable

    @pytest.mark.asyncio
    async def test_mysql_cache_delete_of_unknown_key_changes_nothing(self, sim):
        with pytest.raises(ValueError, match="No se encontraron"):
            await server.edit_mysql_cache("Delete", [999])
        assert len(sim.mysqlcache) == 2

    @pytest.mark.asyncio
    async def test_disabled_backups_do_not_block_writes(self, sim):
        with patch.object(config, "BACKUP_DIR", ""):
            res = json.loads(await server.update_mqtt_settings({"server": "broker"}))
        assert res["aplicado"] is True and res["copia_id"] is None and "aviso" not in res

    @pytest.mark.asyncio
    async def test_backup_failure_warns_but_does_not_block(self, sim, tmp_path):
        blocked = tmp_path / "file"
        blocked.write_text("x")                                        # un fichero donde debería haber un directorio
        with patch.object(config, "BACKUP_DIR", str(blocked / "sub")):
            res = json.loads(await server.update_mqtt_settings({"server": "broker"}))
        assert res["aplicado"] is True and res["copia_id"] is None and "No se pudo guardar la copia" in res["aviso"]

    @pytest.mark.asyncio
    async def test_list_and_get_backup_tools(self, sim):
        a = json.loads(await server.update_mqtt_settings({"server": "b1"}))["copia_id"]
        await server.edit_dayfile("Edit", 1, ["24/08/19", "1"])
        listing = json.loads(await server.list_backups())
        assert listing["activadas"] and {c["tipo"] for c in listing["copias"]} == {"settings", "dayfile"}
        only = json.loads(await server.list_backups(kind="settings"))["copias"]
        assert [c["id"] for c in only] == [a] and "contenido" not in only[0]
        assert json.loads(await server.get_backup(a))["objetivo"] == "mqtt"
        with pytest.raises(ValueError):
            await server.get_backup("../../etc/passwd")


class TestRecordsDecimalSeparator:
    @pytest.mark.asyncio
    async def test_comma_station_gets_the_value_it_expects(self, sim):
        await server.edit_records("alltime", "highTemp", 34.6, "25/07/2019 15:39")
        assert sim.records["alltime"]["highTemp"] == (34.6, "25/07/2019 15:39")

    @pytest.mark.asyncio
    async def test_a_dot_sent_to_a_comma_station_would_be_misread_as_346(self, sim):
        await server._post("/api/edit/alltime", content="name=highTemp&value=34.6&time=x", headers=server.FORM_HEADERS)
        assert sim.records["alltime"]["highTemp"][0] == 346.0

    @pytest.mark.asyncio
    async def test_dot_station(self, backup_dir):
        dot = FakeCumulus(decimal_comma=False)
        with patch.object(server, "_cli", return_value=dot.client()):
            await server.edit_records("alltime", "highTemp", 34.6, "25/07/2019 15:39")
        assert dot.records["alltime"]["highTemp"][0] == 34.6


class TestAuditTrail:
    @pytest.mark.asyncio
    async def test_writes_are_audited_without_secrets(self, sim):
        before = len(safety.recent_audit(500))
        await server.update_mysql_settings({"server": {"pass": "TOP-SECRET-123"}})
        rec = safety.recent_audit(1, "update_mysql_settings")[0]
        assert rec["resultado"] == "ok" and rec["ambito"] == "full" and len(safety.recent_audit(500)) == before + 1
        assert "TOP-SECRET-123" not in json.dumps(rec) and rec["argumentos"]["settings"]["server"]["pass"] == "***" \
            if isinstance(rec["argumentos"]["settings"], dict) else "TOP-SECRET-123" not in rec["argumentos"]["settings"]

    @pytest.mark.asyncio
    async def test_failures_and_denials_are_audited(self, sim, monkeypatch):
        with pytest.raises(ValueError):
            await server.update_settings("program", {"x": 1})
        assert safety.recent_audit(1, "update_settings")[0]["resultado"] == "error"
        monkeypatch.setenv("ENABLE_WRITE_TOOLS", "false")
        out = await server.update_settings("mysql", {"monthly": {"enabled": True}})
        rec = safety.recent_audit(1, "update_settings")[0]
        assert out == config.WRITE_DISABLED_MSG and rec["resultado"] == "denegado"

    @pytest.mark.asyncio
    async def test_audit_file_is_json_lines(self, sim, tmp_path):
        target = tmp_path / "logs" / "audit.jsonl"
        with patch.object(config, "AUDIT_FILE", str(target)):
            await server.update_mqtt_settings({"server": "x"})
            await server.mysql_table("create", "monthly")
        lines = [json.loads(line) for line in target.read_text().splitlines()]
        assert [r["herramienta"] for r in lines] == ["update_mqtt_settings", "mysql_table"]

    @pytest.mark.asyncio
    async def test_get_audit_log_tool(self, sim):
        await server.mysql_table("update", "monthly")
        out = json.loads(await server.get_audit_log(limit=5, tool="mysql_table"))
        assert out["registros"] and all(r["herramienta"] == "mysql_table" for r in out["registros"])

    def test_audit_never_raises(self):
        with patch.object(config, "AUDIT_FILE", "/proc/forbidden/audit.jsonl"):
            assert safety.audit("x", {"a": object()}, "ok", "detalle")["herramienta"] == "x"


class TestReadOnlyScope:
    TOKENS = {"TOKEN": b"full-token", "TOKEN_READONLY": b"ro-token"}

    def scoped(self, token):
        stack = [patch.object(config, k, v) for k, v in self.TOKENS.items()]
        stack.append(patch.object(safety, "bearer_token_from_request", return_value=token))
        return stack

    @pytest.mark.asyncio
    async def test_readonly_token_cannot_write(self, sim):
        ctx = self.scoped(b"ro-token")
        for c in ctx:
            c.start()
        try:
            assert safety.current_scope() == "readonly"
            for call in (server.update_settings("mysql", {"x": 1}), server.edit_dayfile("Edit", 1, ["24/08/19"]),
                         server.mysql_table("create", "monthly"), server.restore_backup("x", dry_run=False)):
                assert await call == safety.READONLY_MSG
        finally:
            for c in reversed(ctx):
                c.stop()
        assert posts(sim) == []
        assert safety.recent_audit(1)[0]["resultado"] == "denegado" and safety.recent_audit(1)[0]["ambito"] in ("readonly", "full")

    @pytest.mark.asyncio
    async def test_readonly_token_can_read_but_secrets_are_masked(self, sim):
        ctx = self.scoped(b"ro-token")
        for c in ctx:
            c.start()
        try:
            out = json.loads(await server.get_settings("mysql"))
            assert out["server"]["pass"] == "***" and out["server"]["host"] == "db"
            assert json.loads(await server.get_mysql_settings())["server"]["pass"] == "***"
            assert (await server.list_backups()) and json.loads(await server.get_tags(["temp"]))
        finally:
            for c in reversed(ctx):
                c.stop()

    @pytest.mark.asyncio
    async def test_full_token_can_write_and_reads_unmasked(self, sim):
        ctx = self.scoped(b"full-token")
        for c in ctx:
            c.start()
        try:
            assert safety.current_scope() == "full"
            assert json.loads(await server.get_settings("mysql"))["server"]["pass"] == "s3cret"
            assert json.loads(await server.update_mqtt_settings({"server": "b"}))["aplicado"] is True
        finally:
            for c in reversed(ctx):
                c.stop()

    def test_unknown_token_is_treated_as_readonly_and_no_request_as_full(self):
        with patch.object(config, "TOKEN", b"full"), patch.object(safety, "bearer_token_from_request", return_value=b"???"):
            assert safety.current_scope() == "readonly"
        with patch.object(safety, "bearer_token_from_request", return_value=None):
            assert safety.current_scope() == "full"      # stdio / sin petición HTTP

    def test_middleware_accepts_both_tokens_and_rejects_others(self):
        with patch.object(config, "TOKEN", b"full-token"), patch.object(config, "TOKEN_READONLY", b"ro-token"):
            with TestClient(app_module.build_http_app()) as client:
                assert client.get("/health").status_code == 200
                assert client.post("/mcp", headers={"Authorization": "Bearer nope"}).status_code == 401
                assert client.post("/mcp").status_code == 401
                for token in ("full-token", "ro-token"):
                    assert client.post("/mcp", headers={"Authorization": f"Bearer {token}"}).status_code != 401

    def test_readonly_token_alone_does_not_open_the_server_when_main_token_is_empty(self):
        with patch.object(config, "TOKEN", b""), patch.object(config, "TOKEN_READONLY", b""):
            with TestClient(app_module.build_http_app()) as client:
                assert client.post("/mcp", headers={"Authorization": "Bearer "}).status_code == 401
