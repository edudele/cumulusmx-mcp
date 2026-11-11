# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/). Releases are published as Docker images
(`edudele/cumulusmx-mcp`) when a `vX.Y.Z` tag is pushed.

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
