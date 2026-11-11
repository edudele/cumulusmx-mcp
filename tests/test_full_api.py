"""Pruebas de la capa de cobertura completa de la API de CumulusMX: ajustes genéricos, MySQL/MQTT, editores de datos,
interval/daily data, consultas, WebSocket y entrada de datos de estaciones HTTP.

Las rutas, nombres de campo y formatos de cuerpo proceden del código fuente de CumulusMX 5.2.0 (Api.cs, DataEditor.cs,
Settings/*.cs). Las escrituras se prueban con mocks: nunca se toca una estación real."""
import asyncio
import json
import os
import sys
import types
import urllib.parse
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))
os.environ.setdefault("MCP_AUTH_TOKEN", "test-secret-token-12345")

import server  # noqa: E402

# Rutas /api/settings/* y /api/setsettings/* tal como aparecen en los `case` de Api.cs (CumulusMX 5.2.0)
OFFICIAL_SETTINGS_GET = {
    "programdata.json", "stationdata.json", "internetdata.json", "thirdpartydata.json", "extrasensordata.json",
    "extrawebfiles.json", "calibrationdata.json", "langdata.json", "noaadata.json", "mysqldata.json", "alarms.json",
    "useralarms.json", "wizard.json", "customlogsintvl.json", "customlogsdaily.json", "displayoptions.json",
    "httpfiles.json", "mqttdata.json",
}
OFFICIAL_SETTINGS_POST = {
    "updateprogramconfig.json", "updatestationconfig.json", "updateinternetconfig.json", "updatethirdpartyconfig.json",
    "updateextrasensorconfig.json", "updatecalibrationconfig.json", "updatenoaaconfig.json", "updateextrawebfiles.json",
    "updatemysqlconfig.json", "updatealarmconfig.json", "updateuseralarms.json", "wizard.json",
    "updatecustomlogsintvl.json", "updatecustomlogsdaily.json", "updatedisplay.json", "updatelanguage.json",
    "updatehttpfiles.json", "updatemqttconfig.json",
}

MYSQL_CURRENT = {
    "accessible": False,
    "server": {"host": "db", "port": 3306, "user": "cumulus", "pass": "s3cret", "database": "weather",
               "advanced": {"sslMode": 1, "tlsVers": "TLS 1.2, TLS 1.3"}},
    "options": {"updateonedit": False, "bufferonerror": True},
    "monthly": {"enabled": False, "table": "Monthly"},
    "realtime": {"enabled": False, "table": "Realtime", "retentionVal": 7, "retentionUnit": "D", "limit1min": False},
}


def decode_body(body: str, style: str = "form") -> dict:
    """Lo que hace CumulusMX: quita el prefijo json= (5 caracteres) si procede y aplica UrlDecode."""
    if style == "form":
        assert body.startswith("json="), "el cuerpo debe empezar por 'json='"
        body = body[5:]
    return json.loads(urllib.parse.unquote(body))


def mocks(current=None, response="success"):
    return (patch.object(server, "_json", new=AsyncMock(return_value=current)),
            patch.object(server, "_post", new=AsyncMock(return_value=response)))


# ---------------------------------------------------------------- tabla de secciones ----

class TestSettingsTable:
    def test_every_official_settings_endpoint_is_mapped(self):
        gets = {g for g, _, _ in server.SETTINGS_SECTIONS.values()}
        posts = {p for _, p, _ in server.SETTINGS_SECTIONS.values()}
        assert gets == OFFICIAL_SETTINGS_GET
        assert posts == OFFICIAL_SETTINGS_POST
        assert len(server.SETTINGS_SECTIONS) == 18

    def test_only_alarms_use_raw_body(self):
        raw = {k for k, (_, _, style) in server.SETTINGS_SECTIONS.items() if style == "raw"}
        assert raw == {"alarms"}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("section", sorted(server.SETTINGS_SECTIONS))
    async def test_get_settings_reads_the_right_path(self, section):
        with patch.object(server, "_get", new=AsyncMock(return_value="{}")) as g:
            await server.get_settings(section)
        assert g.await_args.args[0] == f"/api/settings/{server.SETTINGS_SECTIONS[section][0]}"

    @pytest.mark.asyncio
    async def test_get_settings_rejects_unknown_section(self):
        with pytest.raises(ValueError):
            await server.get_settings("passwords")  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_mysql_and_mqtt_getters(self):
        with patch.object(server, "_get", new=AsyncMock(return_value="{}")) as g:
            await server.get_mysql_settings()
            assert g.await_args.args[0] == "/api/settings/mysqldata.json"
            await server.get_mqtt_settings()
            assert g.await_args.args[0] == "/api/settings/mqttdata.json"


# ---------------------------------------------------------------- secretos ----

class TestSecrets:
    @pytest.mark.asyncio
    async def test_secrets_are_returned_by_default(self):
        with patch.object(server, "_get", new=AsyncMock(return_value=json.dumps(MYSQL_CURRENT))):
            assert json.loads(await server.get_mysql_settings())["server"]["pass"] == "s3cret"

    @pytest.mark.asyncio
    async def test_masking_option_hides_passwords_but_not_other_fields(self):
        data = {"server": {"host": "h", "pass": "x", "password": "y", "passive": True, "apiKey": "k", "port": 1},
                "ftp": [{"username": "u", "password": "p", "phpsecret": "z", "pskFile": ""}]}
        with patch.object(server, "CUMULUS_MASK_SECRETS", True), \
                patch.object(server, "_get", new=AsyncMock(return_value=json.dumps(data))):
            out = json.loads(await server.get_settings("internet"))
        assert out["server"] == {"host": "h", "pass": "***", "password": "***", "passive": True, "apiKey": "***", "port": 1}
        assert out["ftp"][0] == {"username": "u", "password": "***", "phpsecret": "***", "pskFile": ""}

    def test_non_json_is_not_touched_when_masking(self):
        with patch.object(server, "CUMULUS_MASK_SECRETS", True):
            assert server._maybe_mask("ERROR HTTP 500") == "ERROR HTTP 500"


# ---------------------------------------------------------------- update_settings ----

class TestUpdateSettings:
    @pytest.mark.asyncio
    async def test_partial_merge_keeps_other_fields_and_uses_form_body(self):
        jm, pm = mocks(MYSQL_CURRENT)
        with jm, pm as post:
            res = json.loads(await server.update_settings("mysql", {"monthly": {"enabled": True},
                                                                    "server": {"host": "db2"}}))
        assert post.await_args.args[0] == "/api/setsettings/updatemysqlconfig.json"
        assert post.await_args.kwargs["headers"] == server.FORM_HEADERS
        sent = decode_body(post.await_args.kwargs["content"])
        assert sent["monthly"] == {"enabled": True, "table": "Monthly"}
        assert sent["server"]["host"] == "db2" and sent["server"]["pass"] == "s3cret" and sent["server"]["port"] == 3306
        assert sent["realtime"] == MYSQL_CURRENT["realtime"] and sent["options"] == MYSQL_CURRENT["options"]
        assert res["aplicado"] is True and res["respuesta"] == "success"
        assert set(res["campos_modificados"]) == {"monthly.enabled", "server.host"}
        assert res["campos_modificados"]["monthly.enabled"] == {"antes": False, "despues": True}

    @pytest.mark.asyncio
    async def test_special_characters_survive_the_round_trip(self):
        """Un '+' o '%' en una contraseña se corrompía si el cuerpo no se codificaba (CumulusMX aplica UrlDecode)."""
        pwd = "p+ss%20w&rd=é ñ"
        jm, pm = mocks(MYSQL_CURRENT)
        with jm, pm as post:
            await server.update_mysql_settings({"server": {"pass": pwd}})
        assert decode_body(post.await_args.kwargs["content"])["server"]["pass"] == pwd
        assert "&" not in post.await_args.kwargs["content"][5:]  # el cuerpo va codificado

    @pytest.mark.asyncio
    async def test_dry_run_never_posts_and_masks_secrets_in_the_preview(self):
        jm, pm = mocks(MYSQL_CURRENT)
        with jm, pm as post:
            res = json.loads(await server.update_mysql_settings({"server": {"pass": "nueva", "host": "otro"}}, dry_run=True))
        post.assert_not_awaited()
        assert res["aplicado"] is False and "Vista previa" in res["nota"]
        assert res["campos_modificados"]["server.pass"] == {"antes": "***", "despues": "***"}
        assert res["campos_modificados"]["server.host"] == {"antes": "db", "despues": "otro"}

    @pytest.mark.asyncio
    async def test_no_changes_means_no_post(self):
        jm, pm = mocks(MYSQL_CURRENT)
        with jm, pm as post:
            res = json.loads(await server.update_settings("mysql", {"server": {"host": "db"}}))
        post.assert_not_awaited()
        assert res["aplicado"] is False and "Sin cambios" in res["nota"]

    @pytest.mark.asyncio
    async def test_never_writes_blind_when_current_settings_cannot_be_read(self):
        post = AsyncMock(return_value="success")
        with patch.object(server, "_json", new=AsyncMock(side_effect=ValueError("HTTP 500"))), \
                patch.object(server, "_post", new=post):
            with pytest.raises(ValueError, match="HTTP 500"):
                await server.update_settings("mqtt", {"server": "x"})
        post.assert_not_awaited()
        with patch.object(server, "_json", new=AsyncMock(return_value=["inesperado"])), patch.object(server, "_post", new=post):
            with pytest.raises(ValueError, match="inesperada"):
                await server.update_settings("mqtt", {"server": "x"})
        post.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_replace_mode_sends_the_payload_as_is(self):
        jm, pm = mocks(MYSQL_CURRENT)
        with jm, pm as post:
            res = json.loads(await server.update_settings("mysql", {"server": {"host": "solo"}}, replace=True))
        assert decode_body(post.await_args.kwargs["content"]) == {"server": {"host": "solo"}}
        assert res["modo"] == "sustitucion"

    @pytest.mark.asyncio
    async def test_alarms_use_raw_body_and_nest_partial_changes_under_data(self):
        current = {"data": {"BatteryLow": {"enabled": False}, "Sensor": {"enabled": True}}, "units": {"temp": "C"},
                   "email": {"fromEmail": "a@b.c", "password": "x%y"}}
        jm, pm = mocks(current)
        with jm, pm as post:
            await server.update_settings("alarms", {"BatteryLow": {"enabled": True}})
        assert post.await_args.args[0] == "/api/setsettings/updatealarmconfig.json"
        sent = decode_body(post.await_args.kwargs["content"], style="raw")
        assert sent["data"]["BatteryLow"]["enabled"] is True and sent["data"]["Sensor"] == {"enabled": True}
        assert sent["email"]["password"] == "x%y" and sent["units"] == {"temp": "C"}
        assert not post.await_args.kwargs["content"].startswith("json=")

    @pytest.mark.asyncio
    async def test_alarms_top_level_keys_are_merged_directly(self):
        current = {"data": {"A": 1}, "units": {"temp": "C"}, "email": {"x": 1}}
        jm, pm = mocks(current)
        with jm, pm as post:
            await server.update_settings("alarms", {"units": {"temp": "F"}})
        assert decode_body(post.await_args.kwargs["content"], "raw")["units"] == {"temp": "F"}

    @pytest.mark.asyncio
    async def test_wrappers_target_the_right_endpoint(self):
        jm, pm = mocks({"server": "a", "x": 1})
        with jm, pm as post:
            await server.update_mqtt_settings({"server": "broker"})
            assert post.await_args.args[0] == "/api/setsettings/updatemqttconfig.json"
            await server.update_noaa_settings({"x": 2})
            assert post.await_args.args[0] == "/api/setsettings/updatenoaaconfig.json"
            await server.update_settings("wizard", {"x": 3})
            assert post.await_args.args[0] == "/api/setsettings/wizard.json"

    @pytest.mark.asyncio
    async def test_accepts_json_string_and_rejects_bad_input(self):
        jm, pm = mocks({"a": 1})
        with jm, pm as post:
            await server.update_settings("display", '{"a": 2}')
            assert decode_body(post.await_args.kwargs["content"]) == {"a": 2}
            with pytest.raises(ValueError):
                await server.update_settings("display", "{no json")
            with pytest.raises(ValueError):
                await server.update_settings("nope", {"a": 1})  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_respects_enable_write_tools_without_touching_the_network(self, monkeypatch):
        monkeypatch.setenv("ENABLE_WRITE_TOOLS", "false")
        j, p = AsyncMock(), AsyncMock()
        with patch.object(server, "_json", new=j), patch.object(server, "_post", new=p):
            assert "deshabilitadas" in await server.update_settings("mysql", {"a": 1})
        j.assert_not_awaited()
        p.assert_not_awaited()

    def test_diff_masks_secret_paths_even_without_option(self):
        d = server._diff_paths({"mqtt": {"password": "a", "host": "x"}}, {"mqtt": {"password": "b", "host": "y"}})
        assert d["mqtt.password"] == {"antes": "***", "despues": "***"} and d["mqtt.host"] == {"antes": "x", "despues": "y"}


# ---------------------------------------------------------------- MySQL ----

class TestMysql:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("action", ["create", "update"])
    @pytest.mark.parametrize("table", ["monthly", "dayfile", "realtime"])
    async def test_table_endpoints_match_official_names(self, action, table):
        official = {"createmonthlysql.json", "createdayfilesql.json", "createrealtimesql.json",
                    "updatemonthlysql.json", "updatedayfilesql.json", "updaterealtimesql.json"}
        with patch.object(server, "_post", new=AsyncMock(return_value="{}")) as p:
            await server.mysql_table(action, table)
        path = p.await_args.args[0]
        assert path.startswith("/api/setsettings/") and path.rsplit("/", 1)[1] in official

    @pytest.mark.asyncio
    async def test_table_validation_and_write_guard(self, monkeypatch):
        with pytest.raises(ValueError):
            await server.mysql_table("drop", "monthly")  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            await server.mysql_table("create", "users")  # type: ignore[arg-type]
        monkeypatch.setenv("ENABLE_WRITE_TOOLS", "false")
        with patch.object(server, "_post", new=AsyncMock()) as p:
            assert "deshabilitadas" in await server.mysql_table("create", "monthly")
        p.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cache_is_paginated_and_clamped(self):
        with patch.object(server, "_get", new=AsyncMock(return_value="{}")) as g:
            await server.mysql_cache(start=-5, length=10000, search="x" * 500)
        path, params = g.await_args.args
        assert path == "/api/data/mysqlcache.json"
        assert params["start"] == 0 and params["length"] == 500 and len(params["search[value]"]) == 100


# ---------------------------------------------------------------- lecturas simples ----

class TestSimpleReads:
    @pytest.mark.asyncio
    async def test_paths(self):
        with patch.object(server, "_get", new=AsyncMock(return_value="ok")) as g:
            await server.diary_export()
            assert g.await_args.args[0] == "/api/data/diaryexport.txt"
            await server.get_current_conditions_text()
            assert g.await_args.args[0] == "/api/edit/currentcond.json"
            await server.station_info("dateformat")
            assert g.await_args.args[0] == "/api/info/dateformat.txt"
            await server.station_info("csvseparator")
            assert g.await_args.args[0] == "/api/info/csvseparator.txt"
            await server.station_info("version")
            assert g.await_args.args[0] == "/api/info/version.json"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("scope", ["alltime", "monthly", "thismonth", "thisyear"])
    @pytest.mark.parametrize("source,suffix", [("records", ""), ("dayfile", "dayfile"), ("logfile", "logfile")])
    async def test_records_edit_data_paths_match_official_names(self, scope, source, suffix):
        with patch.object(server, "_get", new=AsyncMock(return_value="{}")) as g:
            await server.records_edit_data(scope, source)
        assert g.await_args.args[0] == f"/api/edit/{scope}records{suffix}.json"

    @pytest.mark.asyncio
    async def test_generate_reports_all(self):
        with patch.object(server, "_get", new=AsyncMock(return_value="ok")) as g:
            await server.generate_reports("all")
        assert g.await_args.args[0] == "/api/genreports/all"

    @pytest.mark.asyncio
    async def test_generate_reports_all_respects_write_guard(self, monkeypatch):
        monkeypatch.setenv("ENABLE_WRITE_TOOLS", "false")
        with patch.object(server, "_get", new=AsyncMock()) as g:
            assert "deshabilitadas" in await server.generate_reports("all")
        g.assert_not_awaited()


# ---------------------------------------------------------------- interval_data / daily_data ----

class TestIntervalAndDailyData:
    def test_field_lists_match_official_sizes(self):
        assert len(server.LOG_FILE_FIELDS) == 31 and server.LOG_FILE_FIELDS[2] == "Temperature"
        assert len(server.DAYFILE_FIELD_NAMES) == 59 and server.DAYFILE_FIELD_NAMES[6] == "MaxTemperature"
        assert len(server.EXTRA_LOG_FIELDS) == 135
        ex = server.EXTRA_LOG_FIELDS
        assert (ex[2], ex[12], ex[22], ex[32]) == ("Temp1", "Hum1", "Dewpoint1", "SoilTemp1")
        assert ex.index("Snow24h") == 96 and ex[-1] == "SoilEC16" and ex.index("SoilEC1") == 119

    def test_field_names_become_indices(self):
        assert server._field_indices(["Temperature", "humidity", "WBGT"], "interval") == [2, 3, 30]
        assert server._field_indices(["MaxTemperature", "totalrainfalltoday"], "daily") == [6, 14]
        assert server._field_indices(["Temp1", "SoilEC16"], "interval") == [1002, 1134]   # log extra: +1000
        assert server._field_indices(["7", "datetime"], "interval") == [7, 0]               # índices numéricos
        assert server._field_indices(["DateTime", "TS"], "interval") == [0, 1]              # el log principal prevalece
        with pytest.raises(ValueError, match="desconocido"):
            server._field_indices(["Banana"], "interval")
        with pytest.raises(ValueError):
            server._field_indices([], "daily")

    def test_epoch_seconds_pass_through_and_dates_need_a_timezone(self):
        assert server._to_epoch("1735689600") == 1735689600
        with patch.object(server, "CUMULUS_TIMEZONE", ""):
            with pytest.raises(ValueError, match="CUMULUS_TIMEZONE"):
                server._to_epoch("2025-01-01")

    def test_dates_are_converted_with_the_station_timezone(self):
        with patch.object(server, "CUMULUS_TIMEZONE", "UTC"):
            assert server._to_epoch("2025-01-01") == 1735689600
            assert server._to_epoch("2025-01-01 12:30") == 1735689600 + 12 * 3600 + 30 * 60
            assert server._to_epoch("2025-01-01", end_of_day=True) == 1735689600 + 86399
            with pytest.raises(ValueError, match="inválida"):
                server._to_epoch("01/01/2025")
        try:
            import zoneinfo
            zoneinfo.ZoneInfo("Europe/Madrid")
        except Exception:  # sin base de datos de zonas
            pytest.skip("tzdata no disponible")
        with patch.object(server, "CUMULUS_TIMEZONE", "Europe/Madrid"):
            assert server._to_epoch("2025-01-01") == 1735689600 - 3600        # CET = UTC+1
            assert server._to_epoch("2025-07-01") == 1751328000 - 2 * 3600    # CEST = UTC+2

    def test_table_parsing_converts_numbers_including_decimal_commas(self):
        raw = json.dumps([["Date/Time", "Temperature", "Humidity", "Note"],
                          ["24/08/19 12:00", "20,5", "61", "ok"], ["24/08/19 12:05", "-3.2", "100", ""]])
        out = server._parse_table(raw, 10)
        assert out["campos"] == ["Temperature", "Humidity", "Note"] and out["n_filas"] == 2 and not out["truncado"]
        assert out["filas"][0] == ["24/08/19 12:00", 20.5, 61, "ok"] and out["filas"][1][1] == -3.2
        assert server._parse_table(raw, 1)["truncado"] is True
        assert server._parse_table("[]", 5) == "[]" and server._parse_table("ERROR HTTP 500", 5) == "ERROR HTTP 500"

    @pytest.mark.asyncio
    async def test_interval_data_request(self):
        raw = json.dumps([["Date/Time", "Temperature", "Hum1"], ["x", "1,5", "50"]])
        with patch.object(server, "_get", new=AsyncMock(return_value=raw)) as g:
            res = json.loads(await server.interval_data("1735689600", "1735776000", ["Temperature", "Hum1"]))
        path, params = g.await_args.args
        assert path == "/api/data/intervaldata.json"
        assert params == {"from": 1735689600, "to": 1735776000, "data": "2,1012"}
        assert res["filas"] == [["x", 1.5, 50]]
        with pytest.raises(ValueError, match="anterior"):
            await server.interval_data("1735776000", "1735689600", ["Temperature"])

    @pytest.mark.asyncio
    async def test_daily_data_request_and_error_passthrough(self):
        with patch.object(server, "_get", new=AsyncMock(return_value="ERROR HTTP 404 en x")) as g, \
                patch.object(server, "CUMULUS_TIMEZONE", "UTC"):
            res = await server.daily_data("2025-01-01", "2025-01-31", ["MaxTemperature", "HighWbgt"])
        assert res == "ERROR HTTP 404 en x"
        assert g.await_args.args[1] == {"from": 1735689600, "to": 1735689600 + 30 * 86400 + 86399, "data": "6,57"}


# ---------------------------------------------------------------- records_query / graph_select ----

class TestQueriesAndCharts:
    @pytest.mark.asyncio
    async def test_records_query_body_matches_what_cumulusmx_parses(self):
        with patch.object(server, "_post", new=AsyncMock(return_value='{"value": 3.000, "time": "n/a"}')) as p:
            await server.records_query("HighTemp", "count", period="ThisYear", where=">=", value=30.0)
        assert p.await_args.args[0] == "/api/records/query/dayfile.json"
        sent = decode_body(p.await_args.kwargs["content"])
        assert sent == {"dataname": "HighTemp", "function": "count", "where": ">=", "value": 30.0, "startsel": "ThisYear",
                        "start": None, "end": None, "countfunction": None}

    @pytest.mark.asyncio
    async def test_records_query_validations(self):
        bad = [dict(dataname="HighTemp); DROP", function="max"), dict(dataname="HighTemp", function="median"),
               dict(dataname="HighTemp", function="max", where="; --"), dict(dataname="HighTemp", function="max", period="x y"),
               dict(dataname="HighTemp", function="max", period="User"), dict(dataname="HighTemp", function="max",
                                                                               countfunction="avg2")]
        for kw in bad:
            with pytest.raises(ValueError):
                await server.records_query(**kw)
        with patch.object(server, "_post", new=AsyncMock(return_value="{}")) as p:
            await server.records_query("TotalRain", "sum", period="User", start="01/01/2025", end="31/01/2025")
        assert decode_body(p.await_args.kwargs["content"])["startsel"] == "User"

    def test_query_properties_match_official_list(self):
        official = {"HighGust", "WindRun", "HighAvgWind", "LowTemp", "HighTemp", "AvgTemp", "HighHeatIndex", "HighAppTemp",
                    "LowAppTemp", "LowWindChill", "HighDewPoint", "LowDewPoint", "HighFeelsLike", "LowFeelsLike",
                    "HighHumidex", "LowPress", "HighPress", "HighRainRate", "TotalRain", "HighHourlyRain", "HighRain24h",
                    "LowHumidity", "HighHumidity", "SunShineHours", "HighSolar", "HighUv", "ET", "HeatingDegreeDays",
                    "CoolingDegreeDays", "ChillHours", "HighBgt", "HighWbgt"}
        assert set(server.QUERY_PROPERTIES) == official

    @pytest.mark.asyncio
    async def test_graph_select_paths_and_body(self):
        with patch.object(server, "_post", new=AsyncMock(return_value="{}")) as p:
            await server.graph_select("chart", ["temp", "hum"], "2025-01-01", "2025-01-31")
            assert p.await_args.args[0] == "/api/graphdata/selectachart.json"
            body = json.loads(urllib.parse.unquote(p.await_args.kwargs["content"]))
            assert body == {"series": ["temp", "hum"], "colours": ["#000000", "#000000"], "fromDate": "2025-01-01",
                            "toDate": "2025-01-31"}
            await server.graph_select("period", ["temp"], "2025-01-01", "2025-01-31", colours=["#ff0000"])
            assert p.await_args.args[0] == "/api/graphdata/selectaperiod.json"
        with pytest.raises(ValueError):
            await server.graph_select("chart", [], "2025-01-01", "2025-01-31")
        with pytest.raises(ValueError):
            await server.graph_select("chart", ["temp"], "ayer", "2025-01-31")


# ---------------------------------------------------------------- WebSocket ----

def fake_websockets(messages, error=None, hang=False):
    class FakeWS:
        def __init__(self):
            self.queue = list(messages)

        async def recv(self):
            if hang and not self.queue:
                await asyncio.sleep(30)
            return self.queue.pop(0)

    class Ctx:
        def __init__(self, url, **kw):
            Ctx.url, Ctx.kw = url, kw

        async def __aenter__(self):
            if error:
                raise error
            return FakeWS()

        async def __aexit__(self, *a):
            return False

    mod = types.ModuleType("websockets")
    mod.connect = Ctx
    return mod, Ctx


class TestLiveSample:
    @pytest.mark.asyncio
    async def test_collects_messages_and_builds_ws_url(self):
        mod, ctx = fake_websockets(['{"temp": "10.0"}', '{"temp": "10.1"}', "texto"])
        with patch.dict(sys.modules, {"websockets": mod}), patch.object(server, "BASE", "http://cumulus:8998"), \
                patch.object(server, "_get_auth", return_value=None):
            res = json.loads(await server.live_sample(seconds=5, max_messages=3))
        assert ctx.url == "ws://cumulus:8998/ws/" and res["url"] == ctx.url
        assert res["mensajes"] == [{"temp": "10.0"}, {"temp": "10.1"}, "texto"] and res["tiempo_agotado"] is False

    @pytest.mark.asyncio
    async def test_https_becomes_wss_and_basic_auth_is_sent(self):
        mod, ctx = fake_websockets(["{}"])
        with patch.dict(sys.modules, {"websockets": mod}), patch.object(server, "BASE", "https://x.example"), \
                patch.object(server, "_get_auth", return_value=("user", "pw")):
            await server.live_sample(max_messages=1)
        assert ctx.url == "wss://x.example/ws/"
        assert ctx.kw["additional_headers"]["Authorization"] == "Basic dXNlcjpwdw=="

    @pytest.mark.asyncio
    async def test_returns_partial_messages_on_timeout(self):
        mod, _ = fake_websockets(['{"a": 1}'], hang=True)
        with patch.dict(sys.modules, {"websockets": mod}), patch.object(server, "_get_auth", return_value=None):
            res = json.loads(await server.live_sample(seconds=1, max_messages=5))
        assert res["mensajes"] == [{"a": 1}] and res["tiempo_agotado"] is True

    @pytest.mark.asyncio
    async def test_connection_errors_are_reported_not_raised(self):
        mod, _ = fake_websockets([], error=ConnectionRefusedError("no"))
        with patch.dict(sys.modules, {"websockets": mod}), patch.object(server, "_get_auth", return_value=None):
            res = json.loads(await server.live_sample())
        assert "ConnectionRefusedError" in res["error"]

    @pytest.mark.asyncio
    async def test_limits_are_clamped(self):
        mod, _ = fake_websockets(["{}"] * 30)
        with patch.dict(sys.modules, {"websockets": mod}), patch.object(server, "_get_auth", return_value=None):
            res = json.loads(await server.live_sample(seconds=9999, max_messages=999))
        assert res["n_mensajes"] == 20


# ---------------------------------------------------------------- editores de datos ----

class TestDataEditors:
    @pytest.mark.asyncio
    async def test_edit_dayfile_body_and_cache_invalidation(self):
        server._cached_dayfile_by_year[2024] = [{"x": 1}]
        row = ["24/08/19", "15.0", "142"]
        with patch.object(server, "_post", new=AsyncMock(return_value='{"errors":null}')) as p:
            await server.edit_dayfile("Edit", 3511, row)
        assert p.await_args.args[0] == "/api/edit/dayfile" and p.await_args.kwargs["headers"] == server.JSON_HEADERS
        assert json.loads(p.await_args.kwargs["content"]) == {"action": "Edit", "lines": [3511], "data": [row]}
        assert server._cached_dayfile_by_year == {}

    @pytest.mark.asyncio
    async def test_edit_datalog_recent_and_mysql_cache_bodies(self):
        with patch.object(server, "_post", new=AsyncMock(return_value="ok")) as p:
            await server.edit_datalog("Delete", 12, ["24/08/19", "1566644400", "10,5"], extra=True)
            assert p.await_args.args[0] == "/api/edit/datalogs"
            assert json.loads(p.await_args.kwargs["content"]) == {
                "action": "Delete", "lines": [12], "extra": True, "data": [["24/08/19", "1566644400", "10,5"]]}
            await server.edit_recent_data("Edit", ["a", 1, 2.5])
            assert p.await_args.args[0] == "/api/edit/recentdata"
            assert json.loads(p.await_args.kwargs["content"]) == {"action": "Edit", "data": [["a", "1", "2.5"]]}
            await server.edit_mysql_cache("Edit", [7], ["INSERT 1"])
            assert p.await_args.args[0] == "/api/edit/mysqlcache"
            assert json.loads(p.await_args.kwargs["content"]) == {"action": "Edit", "keys": [7], "statements": ["INSERT 1"]}
            await server.edit_mysql_cache("Delete", [7], ["INSERT 1"])
            assert json.loads(p.await_args.kwargs["content"]) == {"action": "Delete", "keys": [7], "statements": ["INSERT 1"]}

    @pytest.mark.asyncio
    async def test_editor_validations(self):
        with pytest.raises(ValueError):
            await server.edit_dayfile("Edit", 0, ["x"])
        with pytest.raises(ValueError):
            await server.edit_dayfile("Edit", 1, [])
        with pytest.raises(ValueError):
            await server.edit_dayfile("Update", 1, ["x"])  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            await server.edit_datalog("Edit", 1, [])
        with pytest.raises(ValueError):
            await server.edit_recent_data("Edit", [])
        with pytest.raises(ValueError):
            await server.edit_mysql_cache("Edit", [], None)
        with pytest.raises(ValueError, match="exactamente 1 clave"):
            await server.edit_mysql_cache("Edit", [1, 2], ["solo uno"])
        with pytest.raises(ValueError, match="un statement por cada clave"):
            await server.edit_mysql_cache("Delete", [1, 2], ["solo uno"])

    @pytest.mark.asyncio
    async def test_all_editors_respect_the_write_guard(self, monkeypatch):
        monkeypatch.setenv("ENABLE_WRITE_TOOLS", "false")
        calls = [server.edit_dayfile("Edit", 1, ["x"]), server.edit_datalog("Edit", 1, ["x"]),
                 server.edit_recent_data("Edit", ["x"]), server.edit_mysql_cache("Delete", [1]),
                 server.edit_records("alltime", "highTemp", 30.0, "01/01/2025 10:00"),
                 server.diary_upload("cabecera\nregistro"), server.diary_automate(1),
                 server.station_input("ecowitt", "POST", "x=1"), server.mysql_table("create", "monthly")]
        with patch.object(server, "_post", new=AsyncMock()) as p, patch.object(server, "_get", new=AsyncMock()) as g:
            results = await asyncio.gather(*calls)
        assert all("deshabilitadas" in r for r in results)
        p.assert_not_awaited()
        g.assert_not_awaited()


class TestEditRecords:
    @staticmethod
    def server_side_parse(body: str):
        """Réplica de lo que hace DataEditor.EditAllTimeRecs: UnescapeDataString (no convierte '+') y Split('&')."""
        text = urllib.parse.unquote(body.replace("+", "\x00")).replace("\x00", "+")
        parts = text.split("&")
        return [p.split("=")[1] for p in parts[:3]]

    @pytest.mark.asyncio
    async def test_alltime_uses_station_decimal_comma(self):
        with patch.object(server, "_get", new=AsyncMock(return_value='{"temp":"10,0"}')), \
                patch.object(server, "_post", new=AsyncMock(return_value="ok")) as p:
            await server.edit_records("alltime", "highTemp", 34.6, "25/07/2019 15:39")
        assert p.await_args.args[0] == "/api/edit/alltime" and p.await_args.kwargs["headers"] == server.FORM_HEADERS
        assert self.server_side_parse(p.await_args.kwargs["content"]) == ["highTemp", "34,6", "25/07/2019+15:39"]

    @pytest.mark.asyncio
    async def test_decimal_point_stations_and_unknown_default(self):
        for tags in ('{"temp":"10.0"}', '{"temp":"10"}', "ERROR HTTP 500"):
            with patch.object(server, "_get", new=AsyncMock(return_value=tags)), \
                    patch.object(server, "_post", new=AsyncMock(return_value="ok")) as p:
                await server.edit_records("thisyear", "highGust", 53.0, "12/02/2025 17:32")
            assert self.server_side_parse(p.await_args.kwargs["content"])[1] == "53.0"

    @pytest.mark.asyncio
    async def test_monthly_name_carries_the_month_prefix(self):
        with patch.object(server, "_get", new=AsyncMock(return_value='{"temp":"1.5"}')), \
                patch.object(server, "_post", new=AsyncMock(return_value="ok")) as p:
            await server.edit_records("monthly", "highTemp", 14.0, "09/01/2015 22:45", month=1)
        assert p.await_args.args[0] == "/api/edit/monthly"
        assert self.server_side_parse(p.await_args.kwargs["content"])[0] == "1-highTemp"
        with pytest.raises(ValueError, match="month"):
            await server.edit_records("monthly", "highTemp", 14.0, "x")
        with pytest.raises(ValueError, match="month"):
            await server.edit_records("monthly", "highTemp", 14.0, "x", month=13)

    @pytest.mark.asyncio
    async def test_month_name_rain_record_time_format(self):
        with patch.object(server, "_get", new=AsyncMock(return_value="{}")), \
                patch.object(server, "_post", new=AsyncMock(return_value="ok")) as p:
            await server.edit_records("alltime", "highMonthlyRain", 170.6, "Oct 2013")
        assert self.server_side_parse(p.await_args.kwargs["content"])[2] == "Oct+2013"

    def test_field_lists_match_official_cases(self):
        assert len(server.RECORD_FIELDS_COMMON) == 31
        assert "highMonthlyRain" not in server.RECORD_EDIT_FIELDS["thismonth"] and len(server.RECORD_EDIT_FIELDS["thismonth"]) == 30
        for scope in ("alltime", "monthly", "thisyear"):
            assert "highMonthlyRain" in server.RECORD_EDIT_FIELDS[scope]

    @pytest.mark.asyncio
    async def test_invalid_scope_or_field(self):
        with pytest.raises(ValueError, match="no válido"):
            await server.edit_records("thismonth", "highMonthlyRain", 1.0, "x")
        with pytest.raises(ValueError, match="no válido"):
            await server.edit_records("alltime", "highTemp; rm", 1.0, "x")
        with pytest.raises(ValueError, match="scope"):
            await server.edit_records("decade", "highTemp", 1.0, "x")  # type: ignore[arg-type]


class TestDiaryAndStationInput:
    @pytest.mark.asyncio
    async def test_diary_upload_and_automate(self):
        with patch.object(server, "_post", new=AsyncMock(return_value="ok")) as p:
            await server.diary_upload("fecha,texto\n2025-01-01,nieve")
            assert p.await_args.args[0] == "/api/edit/diaryupload"
            assert p.await_args.kwargs["content"] == "fecha,texto\n2025-01-01,nieve"
            await server.diary_automate(2)
            assert p.await_args.args[0] == "/api/edit/diaryautomate" and p.await_args.kwargs["content"] == "2"
            await server.diary_automate(0)
        for bad in ("", "solo cabecera"):
            with pytest.raises(ValueError):
                await server.diary_upload(bad)
        with pytest.raises(ValueError):
            await server.diary_automate(5)

    @pytest.mark.asyncio
    async def test_station_input_post_and_get(self):
        with patch.object(server, "_post", new=AsyncMock(return_value="ok")) as p, \
                patch.object(server, "_get", new=AsyncMock(return_value="ok")) as g:
            await server.station_input("ecowitt", "POST", "tempf=50&humidity=60", content_type="application/x-www-form-urlencoded")
            assert p.await_args.args[0] == "/station/ecowitt" and p.await_args.kwargs["content"] == "tempf=50&humidity=60"
            await server.station_input("wunderground", "GET", query={"tempf": "50", "baromin": "29.9"})
            assert g.await_args.args == ("/station/wunderground", {"tempf": "50", "baromin": "29.9"})
            await server.station_input("ecowitt", "GET")
            assert g.await_args.args == ("/station/ecowitt", None)

    @pytest.mark.asyncio
    async def test_station_input_rejects_unsupported_combinations(self):
        for protocol, method in (("json", "GET"), ("wunderground", "POST"), ("ambient", "POST"), ("nope", "GET")):
            with pytest.raises(ValueError):
                await server.station_input(protocol, method)  # type: ignore[arg-type]

    def test_protocol_table_matches_official_routes(self):
        posts = {p for p, m in server.STATION_INPUT_PROTOCOLS.items() if "POST" in m}
        gets = {p for p, m in server.STATION_INPUT_PROTOCOLS.items() if "GET" in m}
        assert posts == {"ecowitt", "ecowittextra", "json", "jsonextra"}
        assert gets == {"wunderground", "ambient", "ambientextra", "ecowitt", "ecowittextra"}


# ---------------------------------------------------------------- vía protocolo MCP ----

class TestThroughMcpClient:
    @pytest.mark.asyncio
    async def test_new_tools_are_callable_and_validated_by_the_protocol(self):
        from fastmcp import Client
        from fastmcp.exceptions import ToolError

        jm, pm = mocks(MYSQL_CURRENT)
        with jm, pm as post, patch.object(server, "_get", new=AsyncMock(return_value=json.dumps(MYSQL_CURRENT))):
            async with Client(server.mcp) as c:
                res = await c.call_tool("get_settings", {"section": "mysql"})
                assert json.loads(res.content[0].text)["server"]["host"] == "db"
                res = await c.call_tool("update_mysql_settings", {"settings": {"monthly": {"enabled": True}}, "dry_run": True})
                assert json.loads(res.content[0].text)["aplicado"] is False
                post.assert_not_awaited()
                res = await c.call_tool("update_settings", {"section": "mqtt", "settings": {"x": 1}})
                assert json.loads(res.content[0].text)["aplicado"] is True
                with pytest.raises(ToolError):
                    await c.call_tool("get_settings", {"section": "passwords"})
                with pytest.raises(ToolError):
                    await c.call_tool("mysql_table", {"action": "drop", "table": "monthly"})
                with pytest.raises(ToolError):
                    await c.call_tool("records_query", {"dataname": "HighTemp", "function": "max", "where": "; --"})
