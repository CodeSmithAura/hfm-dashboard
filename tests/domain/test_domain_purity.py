"""Ports and adapters: the domain must not import I/O libraries (CLAUDE.md, Architecture)."""

import ast
from pathlib import Path

import pytest

DOMAIN = Path(__file__).resolve().parents[2] / "src" / "hfm_dashboard" / "domain"
FORBIDDEN = {
    "yaml", "httpx", "requests", "pypsrp", "winrm", "pyodbc", "smtplib", "sqlite3",
    "playwright", "fastapi", "apscheduler", "socket", "subprocess", "hfm_dashboard.config",
    "hfm_dashboard.adapters",
}


@pytest.mark.parametrize("module", sorted(DOMAIN.glob("*.py")), ids=lambda p: p.name)
def test_domain_has_no_io_imports(module):
    tree = ast.parse(module.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            names = [node.module or ""]
        else:
            continue
        for name in names:
            assert not any(name == f or name.startswith(f + ".") for f in FORBIDDEN), (
                f"{module.name} imports {name}"
            )
