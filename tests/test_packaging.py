"""Coherencia del empaquetado, Docker y workflows (los errores aquí no los detectan los demás tests)."""
import re
import sys
from pathlib import Path

import pytest
import yaml

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cumulusmx_mcp import __version__  # noqa: E402


def pyproject():
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_version_is_dynamic_and_single_sourced():
    cfg = pyproject()
    assert "version" in cfg["project"]["dynamic"] and cfg["tool"]["setuptools"]["dynamic"]["version"] == {
        "attr": "cumulusmx_mcp.__version__"}
    src = (ROOT / "cumulusmx_mcp" / "__init__.py").read_text(encoding="utf-8")
    assert len(re.findall(r'^__version__ = "', src, flags=re.M)) == 1
    assert not re.search(r"__version__ = ", (ROOT / "server.py").read_text(encoding="utf-8"))


def test_entry_points_and_packages():
    cfg = pyproject()
    assert cfg["project"]["scripts"]["cumulusmx-mcp"] == "cumulusmx_mcp.cli:main"
    packages = set(cfg["tool"]["setuptools"]["packages"])
    on_disk = {"cumulusmx_mcp", "cumulusmx_mcp.tools"}
    assert packages == on_disk
    for p in on_disk:
        assert (ROOT / p.replace(".", "/") / "__init__.py").is_file()


def test_pinned_requirements_match_declared_ranges_and_cover_every_dependency():
    lines = [ln for ln in (ROOT / "requirements.txt").read_text().splitlines() if ln and not ln.startswith("#")]
    pins = {ln.split("==")[0].lower().replace("_", "-"): ln.split("==")[1] for ln in lines}
    assert all("==" in ln for ln in lines)
    for dep in pyproject()["project"]["dependencies"]:
        name = re.split(r"[<>=!~ ]", dep)[0].lower().replace("_", "-")
        assert name in pins, f"{name} no está fijado en requirements.txt"
    assert 3 <= int(pins["fastmcp"].split(".")[0]) < 5


def test_dockerfile_installs_pinned_deps_first_and_runs_the_console_script():
    docker = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert docker.index("requirements.txt") < docker.index("COPY cumulusmx_mcp")
    assert 'CMD ["cumulusmx-mcp"]' in docker and "USER mcp" in docker and "VOLUME /data" in docker
    assert "pip install --no-deps ." in docker
    ignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    for needed_excluded in (".env", ".git", "tests"):
        assert needed_excluded in ignore.split()


def test_compose_mounts_the_data_volume_and_exposes_the_readonly_token():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    mcp = compose["services"]["mcp"]
    assert any(v.endswith(":/data") for v in mcp["volumes"]) and "MCP_AUTH_TOKEN_READONLY" in mcp["environment"]


@pytest.mark.parametrize("workflow", ["docker-publish.yml", "release.yml"])
def test_release_workflows_read_the_version_from_the_package(workflow):
    text = (ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
    assert "cumulusmx_mcp/__init__.py" in text and "server.py" not in text
    # el comando sed de los workflows debe extraer la versión real
    m = re.search(r"sed -n '(s/.*?/p)' ", text)
    assert m
    pattern = re.match(r"s/(.*)/(.*)/p", m.group(1))
    version_line = f'__version__ = "{__version__}"'
    assert re.sub(pattern.group(1).replace(r"\(", "(").replace(r"\)", ")"), r"\1", version_line) == __version__


def test_ci_matrix_covers_both_fastmcp_majors_and_the_docker_image():
    ci = yaml.safe_load((ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8"))
    majors = {entry["fastmcp"].split(".")[0] for entry in ci["jobs"]["tests"]["strategy"]["matrix"]["include"]}
    assert majors == {"3", "4"} and {"lint", "tests", "docker-smoke"} <= set(ci["jobs"])
    assert ci["jobs"]["dependency-audit"]["continue-on-error"] is True


def test_dependabot_watches_pip_actions_and_docker():
    cfg = yaml.safe_load((ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8"))
    assert {u["package-ecosystem"] for u in cfg["updates"]} == {"pip", "github-actions", "docker"}
