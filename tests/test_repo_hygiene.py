"""仓库卫生守卫：测试夹具必须 hermetic，不得依赖仓库根 ``projects/``。

``projects/`` 被 gitignore、内容随本机跑过什么而变；测试若把它当夹具，
换机器或改项目配置后必挂（历史上 ``test_seedream_image`` 就踩过）。
用 AST 只看真实字符串/路径表达式，跳过 docstring 与 ``tmp_path / "projects"``。
"""

from __future__ import annotations

import ast
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent


def _docstring_nodes(tree: ast.AST) -> set[int]:
    out: set[int] = set()
    holders = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    for node in ast.walk(tree):
        if not isinstance(node, holders):
            continue
        body = getattr(node, "body", [])
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            out.add(id(body[0].value))
    return out


def _project_literal_offenders(py: Path) -> list[str]:
    tree = ast.parse(py.read_text(encoding="utf-8"))
    docstrings = _docstring_nodes(tree)
    parents: dict[int, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent

    def _under_tmp_path(node: ast.AST) -> bool:
        parent = parents.get(id(node))
        return (
            isinstance(parent, ast.BinOp)
            and isinstance(parent.op, ast.Div)
            and parent.right is node
            and "tmp_path" in ast.unparse(parent.left)
        )

    offenders: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            val = node.value.replace("\\", "/")
            if (val == "projects" or val.startswith("projects/")) and not _under_tmp_path(node):
                offenders.append(f"{py.name}:{node.lineno} 字符串 {node.value!r}")
    return offenders


def test_no_test_reads_repo_projects_dir():
    """测试不得用仓库根 projects/ 当夹具（tmp_path 下的同名目录可以）。"""
    offenders: list[str] = []
    for py in sorted(_TESTS_DIR.rglob("*.py")):
        if py.name == Path(__file__).name:
            continue  # 守卫自身的匹配字面量不算
        offenders.extend(_project_literal_offenders(py))
    assert not offenders, "测试不得读取仓库 projects/：" + "; ".join(offenders)
