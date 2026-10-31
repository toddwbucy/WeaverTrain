"""The primitives share the manifest format, not each other's internals."""

import ast
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1] / "src" / "weaver_train"
PRIMITIVES = ["manifest", "loader", "trainer", "bench"]


def weaver_imports(module: str) -> set[str]:
    tree = ast.parse((PKG / f"{module}.py").read_text())
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module == "weaver_train":
                found |= {a.name for a in node.names}
            elif node.module.startswith("weaver_train."):
                found.add(node.module.split(".")[1])
            elif node.level:  # relative import
                found.add(node.module.split(".")[0])
        elif isinstance(node, ast.Import):
            found |= {a.name.split(".")[1] for a in node.names if a.name.startswith("weaver_train.")}
    return found


@pytest.mark.parametrize("module", PRIMITIVES)
def test_only_manifest_is_shared(module):
    allowed = set() if module == "manifest" else {"manifest"}
    assert weaver_imports(module) <= allowed


def test_no_other_weavertools_repo():
    for py in PKG.glob("*.py"):
        text = py.read_text()
        for other in ("weaver_agent", "weaver_analysis", "weaver_web", "weaver_types", "weaver-"):
            assert f"import {other}" not in text and f"from {other}" not in text, (py, other)
