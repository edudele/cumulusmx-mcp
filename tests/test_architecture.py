"""Reglas de arquitectura del paquete: capas, referencias entre módulos y nombres únicos.

Existen para que el código siga siendo fácil de modificar: sin ciclos de importación, sin `from .x import funcion` que
rompan los parches de los tests, y con cada nombre público definido en un único módulo (la fachada `server` lo necesita)."""
import ast
import asyncio
import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "cumulusmx_mcp"
sys.path.insert(0, str(ROOT))

# Cada módulo solo puede importar módulos de capas anteriores (índice menor) o de su misma capa dentro de tools/.
LAYERS = ["config", "utils", "safety", "backups", "client", "app", "dayrows", "analysis", "settings_engine", "alerts",
          "reporting"]
TOOLS = ["tools"]          # tools/* (cualquier orden entre ellas, salvo dependencias explícitas)
TOP = ["resources", "prompts", "cli", "_facade"]
# Objetos estables que sí pueden importarse con `from .x import nombre` (no se parchean en los tests)
BARE_ALLOWED = {("app", "mcp"), ("app", "READ_ONLY"), ("app", "WRITE_ACTION"), ("app", "write_tool"), ("config", "logger"),
                ("app", "build_http_app"), ("cli", "main")}   # los dos últimos: punto de entrada


def modules():
    for path in sorted(PKG.rglob("*.py")):
        rel = path.relative_to(PKG).with_suffix("")
        yield ".".join(rel.parts), path


def imported_modules(path, name):
    """Módulos del paquete que importa `name` (rutas relativas al paquete, p. ej. 'client' o 'tools.climate')."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    pkg_parts = name.split(".")[:-1]
    out = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.ImportFrom) and node.level):
            continue
        base = pkg_parts[: len(pkg_parts) - (node.level - 1)]
        if node.module:
            out.append(".".join(base + node.module.split(".")))
        else:
            out += [".".join(base + [a.name]) for a in node.names if not a.name.startswith("__")]
    return out


def layer(name):
    top = name.split(".")[0]
    if top in LAYERS:
        return LAYERS.index(top)
    if top == "tools":
        return len(LAYERS)
    return len(LAYERS) + 1


def test_every_module_is_classified():
    known = set(LAYERS) | set(TOP) | {"tools", "__init__", "__main__"}
    assert {n.split(".")[0] for n, _ in modules()} <= known


def test_no_module_imports_from_a_higher_layer():
    problems = []
    for name, path in modules():
        if name in ("__init__", "__main__"):
            continue
        for target in imported_modules(path, name):
            if target and layer(target) > layer(name):
                problems.append(f"{name} importa {target} (capa superior)")
    assert not problems, "\n".join(problems)


def test_cross_module_references_use_module_attributes():
    """`from .client import _get` copiaría la función y los parches de los tests (y de quien extienda el código) no
    surtirían efecto. Solo se permiten las excepciones de BARE_ALLOWED."""
    problems = []
    module_names = {n for n, _ in modules()}
    for name, path in modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level and node.module:
                mod = node.module.split(".")[-1]
                for alias in node.names:
                    if f"{node.module}.{alias.name}" in module_names:      # importar un módulo es lo correcto
                        continue
                    if (mod, alias.name) not in BARE_ALLOWED:
                        problems.append(f"{name}: from {'.' * node.level}{node.module} import {alias.name}")
    assert not problems, "\n".join(problems)


def test_public_names_are_defined_in_a_single_module():
    """La fachada `server` resuelve cada nombre en el módulo que lo define: un nombre repetido sería ambiguo."""
    seen: dict[str, str] = {}
    clashes = []
    for name, path in modules():
        if name in ("__init__", "__main__", "_facade"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            names = []
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names = [node.name]
            elif isinstance(node, ast.Assign):
                names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names = [node.target.id]
            for n in names:
                if n in seen and seen[n] != name:
                    clashes.append(f"{n}: {seen[n]} y {name}")
                seen[n] = name
    assert not clashes, "\n".join(clashes)


def test_every_tool_has_a_real_description():
    server = importlib.import_module("server")
    tools = asyncio.run(server.mcp.list_tools())
    short = [t.name for t in tools if len((t.description or "").strip()) < 30]
    assert not short, f"tools sin descripción útil: {short}"


def test_tool_modules_register_into_the_shared_app():
    server = importlib.import_module("server")
    names = {t.name for t in asyncio.run(server.mcp.list_tools())}
    for expected in ("snapshot", "update_settings", "restore_backup", "weather_alerts", "period_report", "edit_dayfile"):
        assert expected in names


def test_server_shim_still_works_as_a_script():
    import subprocess
    out = subprocess.run([sys.executable, str(ROOT / "server.py"), "--version"], capture_output=True, text=True, timeout=60,
                         cwd=ROOT)
    assert out.returncode == 0 and out.stdout.startswith("cumulusmx-mcp ")


def test_facade_resolves_patches_and_unknown_names():
    server = importlib.import_module("server")
    from cumulusmx_mcp import client
    original = client._get
    server._get = "parche"
    assert client._get == "parche"
    del server._get
    server._get = original
    assert client._get is original
    with pytest.raises(AttributeError):
        server.no_existe_este_nombre
    assert "update_settings" in dir(server)
