# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/). Releases are published as Docker images
(`edudele/cumulusmx-mcp`) when a `vX.Y.Z` tag is pushed.

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
- Tool annotations `readOnlyHint` / `destructiveHint` on every tool (client-side permission grouping).
- Tests that keep the release coherent: `CHANGELOG.md` and `__version__` must match, and the skill must document
  every registered tool.

### Changed
- `rankings`: when `start_year` is omitted it now defaults to `CUMULUS_DATA_START_YEAR`
  (previously a hard-coded 2023 ignored that setting).
- `GET /health` now also returns the `version` field (existing fields are unchanged).

## [1.0.0]

### Added
- Initial release: FastMCP server for CumulusMX with 47 tools, 4 resources and 4 prompts, Bearer token auth,
  prefix stripping for reverse proxies and Docker image.
