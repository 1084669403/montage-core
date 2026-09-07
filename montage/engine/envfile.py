"""envfile — 从仓库根加载 .env（标准库，不引入 python-dotenv）。

只在 CLI 入口调用，不要在 BaseTool 导入时加载（否则 minitest 会吃到真密钥）。
空值跳过；已存在的环境变量不覆盖。MONTAGE_SKIP_DOTENV=1 或处于 pytest 用例中时不读文件。
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Iterable

_SKIP = "MONTAGE_SKIP_DOTENV"
_NAME_RE = re.compile(r'(?m)^name\s*=\s*["\']montage-core["\']')
_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_loaded = False
_last_applied: list[str] = []


def find_repo_root(*starts: Path | str | None) -> Path | None:
    """向上找含 pyproject.toml（name=montage-core）的目录。"""
    seen: set[Path] = set()
    candidates: list[Path] = []
    for raw in starts:
        if raw:
            candidates.append(Path(raw))
    candidates.append(Path.cwd())
    candidates.append(Path(__file__).resolve())
    for start in candidates:
        cur = start if start.is_dir() else start.parent
        for _ in range(12):
            cur = cur.resolve()
            if cur in seen:
                break
            seen.add(cur)
            pyproject = cur / "pyproject.toml"
            if pyproject.is_file():
                try:
                    text = pyproject.read_text(encoding="utf-8")
                except OSError:
                    text = ""
                if _NAME_RE.search(text):
                    return cur
            if cur.parent == cur:
                break
            cur = cur.parent
    return None


def parse_env_text(text: str) -> dict[str, str]:
    """解析 KEY=value；空值/注释跳过。不去插值、不支持多行。"""
    text = text.lstrip("\ufeff")
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not _KEY_RE.match(key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        value = value.strip()
        if not value:
            continue
        out[key] = value
    return out


def apply_env(values: dict[str, str], environ: dict[str, str] | None = None) -> list[str]:
    """写入 environ；已有非空值不覆盖。返回新写入的键名（不含值）。"""
    env = environ if environ is not None else os.environ
    applied: list[str] = []
    for key, value in values.items():
        if not value:
            continue
        existing = env.get(key)
        if existing:
            continue
        env[key] = value
        applied.append(key)
    return applied


def ensure_dotenv(root: Path) -> Path | None:
    """仓库根没有 .env 且有 .env.example 时复制一份；已有则不动。"""
    dest = root / ".env"
    src = root / ".env.example"
    if dest.exists() or not src.is_file():
        return dest if dest.exists() else None
    shutil.copyfile(src, dest)
    return dest


def load_envfile(
    *,
    root: Path | str | None = None,
    environ: dict[str, str] | None = None,
    force: bool = False,
) -> list[str]:
    """读仓库根 .env 注入环境。返回新写入的键名。"""
    global _loaded, _last_applied
    env = environ if environ is not None else os.environ
    if not force:
        if env.get(_SKIP) == "1" or env.get("PYTEST_CURRENT_TEST"):
            return []
        if environ is None and _loaded:
            return []
    found = Path(root) if root else find_repo_root()
    if found is None:
        return []
    root_path = found
    if environ is None:
        ensure_dotenv(root_path)
    path = root_path / ".env"
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    applied = apply_env(parse_env_text(text), env)
    if environ is None:
        _loaded = True
        _last_applied = list(applied)
    return applied


def injected_key_names() -> list[str]:
    """最近一次进程级 load 新写入的键名（不含值）。第二次 load 因幂等返回 [] 时仍可读这里。"""
    return list(_last_applied)


def injected_key_count() -> int:
    return len(_last_applied)


def listed_env_names(example_text: str) -> set[str]:
    """example 里出现过的键（含注释掉的 KEY=）。"""
    names: set[str] = set()
    for raw in example_text.splitlines():
        line = raw.strip().lstrip("#").strip()
        if line.lower().startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key = line.partition("=")[0].strip()
        if _KEY_RE.match(key):
            names.add(key)
    return names


def collect_tool_env_keys(tools: Iterable[object]) -> set[str]:
    names: set[str] = set()
    for tool in tools:
        for key in getattr(tool, "env_keys", ()) or ():
            if key:
                names.add(str(key))
    return names
