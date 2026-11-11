# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/). Releases are published as Docker images
(`edudele/cumulusmx-mcp`) when a `vX.Y.Z` tag is pushed.

## [1.8.0]

### Added
- **Backups for every history editor.** `edit_datalog`, `edit_recent_data`, `edit_mysql_cache` and `edit_records` now save the
  previous state before writing (new module `capture`, which reads it from CumulusMX), and `restore_backup` undoes any of the
  *edits* automatically: day-file and interval-log lines, recent-data records, MySQL-cache statements and records (previous
  value and time, with the station's decimal separator). Deleting rows cannot be re-inserted through CumulusMX's API, so a
  delete backup is kept for reference only. `list_backups` accepts the new kinds.
- **Multi-architecture Docker image** (`linux/amd64` and `linux/arm64`: Raspberry Pi, NAS, Apple Silicon servers), with an
  emulated arm64 smoke test in CI.
- **Test coverage in CI** (job `coverage`, minimum 90 %, report in the run summary and as an artifact).

### Changed
- **The read-only token no longer sees the write tools**: `tools/list` omits them for that token (the guard that refuses and
  audits any attempt to run one is unchanged).
- **Oversized responses are truncated into valid JSON.** Instead of cutting the text in the middle (which broke the parsing),
  a response over 60,000 characters comes back as `{"truncado": true, "caracteres_totales": …, "filas_totales": …,
  "filas_devueltas": …, "contenido": …}` with as many rows as fit.
- `edit_mysql_cache`: *Edit* requires exactly one key and one statement (CumulusMX only processes the first); *Delete* reads
  the statements from the cache when they are not given (the real server fails with a 500 without them) and refuses unknown keys.
- The test simulator now serves the interval logs, recent data, MySQL cache and every records scope with the real formats.

## [1.7.0]

### Added
- **Safety net for the write tools**
  - Every write is audited (tool, token scope, arguments with secrets masked, result `ok` / `fallo` / `denegado` / `error`)
    in the log and, with `CUMULUS_AUDIT_FILE`, in a JSON-Lines file. New tool `get_audit_log`.
  - Automatic backups before changing a settings section, a day-file line or the records (`CUMULUS_BACKUP_DIR`,
    `CUMULUS_BACKUP_KEEP`; files `0600`). New tools `list_backups`, `get_backup` and `restore_backup` (settings and day-file
    lines; `dry_run` by default; a restore is itself undoable). `edit_dayfile` and `edit_records` now return a JSON envelope
    with `respuesta` and `copia_id`.
  - Optional read-only token `MCP_AUTH_TOKEN_READONLY`: reads work, writes are refused, settings come back with passwords masked.
- `weather_alerts`: frost (registered today / in progress / night risk), strong wind, intense rain, heat, UV, fast pressure drop,
  dry hot air and station without data, with overridable thresholds and a warning when the station is not metric.
- `period_report`: shareable Markdown report for a week, month or year, with a comparison with the station's own normals.
- `daily_briefing` also returns the CumulusMX forecast (`forecast`) and cloud base.
- **Runs anywhere**: `cumulusmx-mcp` console script, `python -m cumulusmx_mcp`, `--transport stdio` for local clients
  (Claude Desktop...), `--host`, `--port`, `--version`; installable with `pip` / `uvx`.
- Pinned runtime dependencies (`requirements.txt`), Dependabot, ruff, a Docker image smoke test and an informational
  `pip-audit` job in CI, and a CI matrix of Python 3.10 / 3.12 / 3.13 x FastMCP 3.x / 4.x (both majors pass the whole suite).
- `scripts/smoke_readonly.py`: read-only smoke test of a deployed server.
- `docs/ARCHITECTURE.md` and `CONTRIBUTING.md`.

### Changed
- **The code is now a package.** `server.py` (2800 lines) is split into `cumulusmx_mcp/`: config, utils, safety, backups, client,
  app, dayrows, analysis, settings_engine, alerts, reporting, `tools/*`, prompts, resources and cli. `server.py` remains as a shim:
  `python server.py` and `import server` keep working. Tests enforce the layering and that modules reference each other through
  the module (so patches work).
- The classic `update_station_settings`, `update_calibration_settings` and `update_alarm_settings` use the same safe engine as
  `update_settings`: they no longer overwrite a section with just the fragment when the current settings cannot be read
  (they raise instead), and they keep a backup. Their text response is unchanged.
- The Docker image installs the pinned dependencies and the package, starts with `cumulusmx-mcp`, and declares `/data`
  (backups and audit file). The compose example mounts a volume and exposes the read-only token.
- Release workflows read the version from `cumulusmx_mcp/__init__.py`.
- FastMCP's PyPI update check is disabled by default (`FASTMCP_CHECK_FOR_UPDATES=off`); stdio mode prints no banner.

### Fixed
- `edit_records` detected the station's decimal separator with a pattern that required compact JSON; with spaces it would have
  sent `34.6` to a decimal-comma station, which reads it as 346. Found by the new CumulusMX simulator.
- `dayrows._agg` raised `KeyError` on day-file rows shorter than the current format.

## [1.6.0]

Full coverage of the CumulusMX API (endpoint names, parameters and request formats verified in the 5.2.0 source).

### Added
- Settings: `get_settings(section)` and `update_settings(section, settings, dry_run, replace)` for all 18 sections
  (`program`, `station`, `internet`, `thirdparty`, `extrasensors`, `extrawebfiles`, `calibration`, `language`, `noaa`, `mysql`,
  `alarms`, `useralarms`, `wizard`, `customlogs_interval`, `customlogs_daily`, `display`, `httpfiles`, `mqtt`). The update reads
  the current section, deep-merges only the given fields, returns the changed paths (before/after, secrets masked), supports a
  `dry_run` preview and never writes if the current settings cannot be read.
- MySQL and MQTT: `get_mysql_settings`, `update_mysql_settings`, `mysql_table` (create/update the monthly, dayfile and realtime
  tables), `mysql_cache`, `edit_mysql_cache`, `get_mqtt_settings`, `update_mqtt_settings`; also `update_noaa_settings`.
- Data access: `interval_data` and `daily_data` (field selection by official names, readable dates with `CUMULUS_TIMEZONE` or Unix
  seconds, numbers with regional decimal commas converted), `records_query` (server-side day-file queries), `graph_select`
  (Select-a-Chart / Select-a-Period), `live_sample` (WebSocket `/ws/`), `records_edit_data`, `diary_export`,
  `get_current_conditions_text`.
- History corrections and diary: `edit_dayfile`, `edit_datalog`, `edit_recent_data`, `edit_records` (value sent with the station's
  decimal separator), `diary_upload`, `diary_automate`.
- `station_input`: HTTP station endpoints (`ecowitt`, `ecowittextra`, `json`, `jsonextra`, `wunderground`, `ambient`, `ambientextra`).
- `generate_reports(report_type="all")`; `station_info` accepts `dateformat` and `csvseparator`.
- `CUMULUS_TIMEZONE` and `CUMULUS_MASK_SECRETS` (mask passwords/keys in settings reads; off by default).
- Tool count: 83 (55 read-only, 28 write). `websockets` and `tzdata` added to the Docker image and the CI install.

### Fixed
- `update_alarm_settings` sent the JSON body unencoded, but CumulusMX URL-decodes it, so a `+` or `%` in a value (for example an
  SMTP password) was corrupted. The body is now URL-encoded.

## [1.5.0]

### Added
- `climatology`: monthly normals (temperatures, rain, rain days, sun hours, extremes, record years) from complete months.
- `frost_dates`: last spring frost, first autumn frost, frost days and frost-free period per year, plus average dates
  (complete years only); northern and southern hemisphere modes.
- `degree_days`: growing degree days with base/upper temperature and `average` / `modified` methods, by month and year.
- `wind_rose`: 16-sector frequency of the dominant daily wind direction and peak gust per gust direction.
- `daily_briefing`: live conditions, yesterday in context (percentile/rank vs the same month in other years), month-to-date
  anomaly of temperature and rain, and streaks in progress, in one call. Degrades gracefully if live data or history fails.
- MCP server `instructions` sent to clients (tool selection, conventions, write-tool etiquette) and three guided prompts:
  `resumen_mensual`, `heladas_y_cultivo`, `auditoria_datos`.
- `CUMULUS_TIMEOUT` (default 30 s) and `CUMULUS_RETRIES` (default 2).

### Changed
- GET requests to CumulusMX are retried with exponential backoff (0.3 s, 0.6 s) on connection errors, timeouts and
  HTTP 502/504. 4xx, 503 ("station not running") and all POST requests are never retried.
- `ENABLE_WRITE_TOOLS=false` now removes the 15 write tools from the published tool list instead of only making them
  refuse to run (the runtime guard stays as a second layer). Tool count: 58 (43 read-only, 15 write).

## [1.4.0]

Verified against the official Cumulus MX Local API wiki, the CumulusMX 5.2.0 source and a live 5.2.0 station.

### Added
- `graph_summary` reports the unit of each series (`unidad`) using the station's `units.json`
  (flat format of 5.2.0 and the older nested format); it degrades gracefully if units cannot be read.
- Day-file columns `lluvia_24h_max`, `bgt_max`, `wbgt_max` (+ `hora_*`), aliases `rain24h`, `bgt`, `wbgt`.
  The 53 existing columns were checked against CumulusMX's `DayfileFieldNames`: order is identical.
- `data_quality` flags days with 0 % humidity as a probable sensor fault.

### Changed
- `get_tags` and `snapshot` now request tags with CumulusMX's `rc` parameter, so numbers arrive as `10.0`
  instead of the regional `"10,0"`. `get_tags(plain_numbers=false)` restores the previous behaviour.

### Fixed
- Extreme values no longer list every tied date: at most 10 are returned plus `n_fechas` with the total.
  A `summary_only` request on a column with hundreds of ties used to return several kilobytes of dates.
- "No data" sentinels of CumulusMX (`-9999` and `9999`, also `<= -999`) are now ignored; only `-9999` was before,
  so a low default of `9999` could pollute rankings and averages.

## [1.3.0]

Endpoints and names below were verified against the CumulusMX source (`Api.cs`) rather than assumed.

### Added
- `simple_report`: the seven built-in CumulusMX summary reports (`simpleTemperature`, `simpleRainfall`,
  `simpleWindRun`, `simpleSunshine`, `simpleET`, `simpleDryDays`, `simpleWetDays`).
- `extra_sensors`: current values of the 22 extra-sensor endpoints (soil, leaf wetness, AirLink, lightning, CO2, laser depth, snow, BGT...).

### Fixed
- `api_get` whitelist only allowed lower-case report names, so `reports/simpleTemperature` and the other
  `simple*` reports were rejected. The whitelist now accepts them (settings, utils and report-generation paths stay blocked, with tests).

### Changed
- `graph_data` and `daily_graph_data` descriptions list the full set of series names CumulusMX provides
  (including extra-sensor and per-interval series) instead of a partial list.
- The skill documents the new tools; the tool count is now 53 (38 read-only, 15 write).

## [1.2.0]

### Added
- `count_days`: days matching a condition on any numeric day-file column (alias-aware), with per-year breakdown.
- `find_streaks`: longest runs of consecutive days matching a condition, plus the run in progress if any.
- `anomaly`: compares a period with the station's own climatology (same calendar window in the other years):
  normal, difference (and % for accumulated variables), percentile and rank. In-progress periods are compared on the
  elapsed part only; years with under 80 % coverage are skipped; 29 February is handled.
- `data_quality`: missing days, days without temperature and incoherent values in the daily history.
- These four are computed from the cached day file, so they need no additional CumulusMX endpoints.
- Tests that call the new tools through a real in-memory MCP client (schema validation and serialization included).

### Changed
- `today_all` and `records_all` now issue their 6 / 5 CumulusMX requests concurrently instead of one by one.
- The skill (`SKILL.md`, `reference.md`) documents the new tools; the tool count is now 51 (36 read-only, 15 write).

## [1.1.0]

### Added
- Agent skill `skills/cumulusmx-weather/` (`SKILL.md` + `reference.md`): tells an AI agent which tool to use for each
  kind of question, how the Spanish column names and English aliases map, and how to handle the 15 write tools safely.
  Compatible with the Agent Skills format used by Claude Code and other agents.
- `__version__` in `server.py`, reported by the MCP server itself, by `server_status` (`mcp_version`),
  by `GET /health` (`version`) and in the startup log.
- Tests that keep the release coherent: `CHANGELOG.md` and `__version__` must match, and the skill must document
  every registered tool.

### Changed
- `rankings`: when `start_year` is omitted it now defaults to `CUMULUS_DATA_START_YEAR`
  (previously a hard-coded 2023 ignored that setting).
- `GET /health` now also returns the `version` field (existing fields are unchanged).

## [1.0.1]

### Added
- Tool annotations `readOnlyHint` / `destructiveHint` on every tool (client-side permission grouping in Claude and
  other MCP clients).

## [1.0.0]

### Added
- Initial release: FastMCP server for CumulusMX with 47 tools, 4 resources and 4 prompts, Bearer token auth,
  prefix stripping for reverse proxies and Docker image.
