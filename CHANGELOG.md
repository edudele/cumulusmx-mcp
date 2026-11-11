# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/). Releases are published as Docker images
(`edudele/cumulusmx-mcp`) when a `vX.Y.Z` tag is pushed.

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
