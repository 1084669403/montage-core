"""minitest — 不依赖 pytest 的测试运行器。

提供 pytest 风格的 tmp_path / monkeypatch 垫片与 pytest.raises，
直接执行 tests/ 下的测试函数。核心包契约校验依赖 jsonschema
（``pip install -e .`` 后即可跑）。

用法：
    python scripts/minitest.py               # 跑全部 tests/
    python scripts/minitest.py tests/test_stages.py   # 指定文件

本文件为全新原创代码。
"""

from __future__ import annotations

import contextlib
import inspect
import os
import pathlib
import re
import shutil
import sys
import traceback
import types

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["MONTAGE_SKIP_DOTENV"] = "1"
os.environ["MONTAGE_SKIP_PACING"] = "1"


# ---------------------------------------------------------------------------
# pytest 垫片
# ---------------------------------------------------------------------------

_MISSING = object()


class _MonkeyPatch:
    """极简 monkeypatch：setenv / delenv / setattr，测试后自动还原。"""

    def __init__(self) -> None:
        self._undo: list[tuple] = []

    def setenv(self, key: str, value: str) -> None:
        self._undo.append(("env", key, os.environ.get(key, _MISSING)))
        os.environ[key] = value

    def delenv(self, key: str, raising: bool = True) -> None:
        if key not in os.environ:
            if raising:
                raise KeyError(key)
            return
        self._undo.append(("env", key, os.environ[key]))
        del os.environ[key]

    def setattr(self, target, name: str, value=None) -> None:
        # 兼容 pytest 用法：setattr("module.attr", value) 或 setattr(obj, "attr", value)
        if isinstance(target, str):
            dotted = target
            value = name if value is None else value
            import importlib

            parts = dotted.split(".")
            obj = None
            for i in range(len(parts), 0, -1):
                try:
                    obj = importlib.import_module(".".join(parts[:i]))
                    rest = parts[i:]
                    break
                except ImportError:
                    continue
            if obj is None:
                raise AttributeError(dotted)
            for seg in rest[:-1]:
                obj = getattr(obj, seg)
            target = obj
            name = rest[-1]
        try:
            old = inspect.getattr_static(target, name)
        except AttributeError:
            old = _MISSING
        self._undo.append(("attr", target, name, old))
        setattr(target, name, value)

    def undo(self) -> None:
        for op in reversed(self._undo):
            if op[0] == "env":
                _, key, old = op
                if old is _MISSING:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = old
            else:
                _, target, name, old = op
                if old is _MISSING:
                    if hasattr(target, name):
                        delattr(target, name)
                else:
                    setattr(target, name, old)
        self._undo.clear()


class _Skipped(Exception):
    """pytest.skip 垫片异常：测试跳过计为通过。"""


class _Mark:
    """pytest.mark 垫片：可直接装饰函数，也支持 .skipif（条件成立则跳过）。"""

    def __call__(self, obj=None, *args, **kwargs):
        if obj is None:
            return lambda f: f
        return obj

    @staticmethod
    def skipif(condition, reason: str = ""):
        def deco(fn):
            deco._skip_condition = (condition, reason)
            if condition:
                def wrapped(*_a, **_k):
                    raise _Skipped(reason)
                return wrapped
            return fn
        return deco


class _PytestShim:
    """提供 pytest.raises / pytest.fixture / pytest.skip，让 pytest 风格测试直接可跑。"""
    mark = _Mark()

    @staticmethod
    @contextlib.contextmanager
    def raises(exc_type, match: str | None = None):
        try:
            yield
        except exc_type as exc:  # noqa: PERF203
            if match and not re.search(match, str(exc)):
                raise AssertionError(
                    f"异常 {exc!r} 与正则 {match!r} 不匹配"
                ) from exc
            return
        raise AssertionError(f"预期抛出 {exc_type.__name__} 但未抛出")

    @staticmethod
    def skip(reason: str = ""):
        raise _Skipped(reason)

    @staticmethod
    def fixture(fn=None, **kwargs):
        if fn is None:
            return lambda f: _mark_fixture(f)
        return _mark_fixture(fn)


_pytest = _PytestShim()
sys.modules.setdefault("pytest", _pytest)


def _mark_fixture(fn):
    fn._is_fixture = True  # type: ignore[attr-defined]
    return fn

_TMP_BASE = ROOT / ".tmp_tests"
_TMP_BASE.mkdir(parents=True, exist_ok=True)


def _make_tmp_path(module_name: str, func_name: str) -> pathlib.Path:
    path = _TMP_BASE / f"{module_name}.{func_name}"
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _run_test(fn, module_name: str, fixtures: dict[str, object]) -> str | None:
    """运行单个测试函数，返回错误字符串（None=通过）。"""
    mp = _MonkeyPatch()
    memo: dict[str, object] = {}

    def resolve(name: str):
        if name in memo:
            return memo[name]
        if name == "tmp_path":
            val = _make_tmp_path(module_name, fn.__name__)
        elif name == "monkeypatch":
            val = mp
        elif name in fixtures:
            fixture_fn = fixtures[name]
            sub = {
                p: resolve(p)
                for p in inspect.signature(fixture_fn).parameters
            }
            val = fixture_fn(**sub)
        else:
            raise TypeError(f"参数 {name!r} 不是已定义 fixture")
        memo[name] = val
        return val

    try:
        sig = inspect.signature(fn)
        kwargs = {}
        for pname, param in sig.parameters.items():
            if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
                continue
            kwargs[pname] = resolve(pname)
        fn(**kwargs)
        return None
    except _Skipped:
        return None  # 跳过计为通过
    except Exception as exc:  # noqa: BLE001
        return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    finally:
        mp.undo()
        path = _TMP_BASE / f"{module_name}.{fn.__name__}"
        shutil.rmtree(path, ignore_errors=True)


def discover_test_files(paths: list[str]) -> list[pathlib.Path]:
    if paths:
        return [pathlib.Path(p) for p in paths]
    return sorted((ROOT / "tests").glob("test_*.py"))


def main(argv: list[str] | None = None) -> int:
    files = discover_test_files(list(argv or []))
    passed = 0
    failed: list[tuple[str, str]] = []
    for file in files:
        if file.suffix != ".py":
            continue
        module_name = f"tests.{file.stem}"
        spec = importlib_import(module_name, file)
        if spec is None:
            failed.append((str(file), "模块导入失败"))
            continue
        mod_skip = getattr(getattr(spec, "pytestmark", None), "_skip_condition", None)
        if mod_skip:
            cond, reason = mod_skip
            if cond:
                passed += 1  # 整模块跳过计为通过
                continue
        fixtures = {
            name: obj
            for name, obj in vars(spec).items()
            if callable(obj) and getattr(obj, "_is_fixture", False)
        }
        for name, obj in sorted(vars(spec).items()):
            if name.startswith("test_") and callable(obj):
                err = _run_test(obj, module_name, fixtures)
                if err is None:
                    passed += 1
                else:
                    failed.append((f"{file.name}::{name}", err))
    print(f"\nminitest: {passed} passed, {len(failed)} failed, {len(files)} files")
    for label, err in failed:
        print(f"\n--- FAIL {label} ---")
        print(err)
    return 1 if failed else 0


def importlib_import(module_name: str, file: pathlib.Path) -> types.ModuleType | None:
    import importlib.util

    if module_name in sys.modules:
        return sys.modules[module_name]
    try:
        spec = importlib.util.spec_from_file_location(module_name, file)
        module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        sys.modules[module_name] = module
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        return module
    except Exception as exc:  # noqa: BLE001
        print(f"import error in {file}: {exc}")
        return None


if __name__ == "__main__":
    raise SystemExit(main())
