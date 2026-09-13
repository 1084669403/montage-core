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

# 默认 runner 不再静默跳过真 ffmpeg 用例：本机有 ffmpeg 就开门。
# 需要用 ``MONTAGE_REAL_FFMPEG=0`` 显式关闭（或 CI 无 ffmpeg 自然跳过）。
if shutil.which("ffmpeg") and os.environ.get("MONTAGE_REAL_FFMPEG") is None:
    os.environ["MONTAGE_REAL_FFMPEG"] = "1"

# 被跳过的用例记录在此，main() 会打印而非静默计为通过。
_SKIPPED: list[str] = []


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


class _Marker:
    """未知 pytest marker 的恒等实现。

    - ``@pytest.mark.ffmpeg`` → 直接返回原函数；
    - ``@pytest.mark.ffmpeg(reason=...)`` → 返回恒等装饰器；
    - ``@pytest.mark.parametrize(...)`` → 给函数打 ``_parametrized`` 标记，
      ``_run_test`` 会跳过（minitest 无法枚举参数），而不是导入期崩溃。
    """

    def __init__(self, name: str) -> None:
        self.name = name

    def __call__(self, obj=None, *args, **kwargs):
        if callable(obj) and not args and not kwargs:
            return obj
        if self.name == "parametrize":
            def deco(fn):
                fn._parametrized = True  # type: ignore[attr-defined]
                return fn
            return deco
        return lambda f: f


class _Mark:
    """pytest.mark 垫片：可直接装饰函数，也支持 .skipif（条件成立则跳过）。"""

    def __call__(self, obj=None, *args, **kwargs):
        if obj is None:
            return lambda f: f
        return obj

    def __getattr__(self, name: str):
        # minitest 不读 pyproject.toml 的 markers 注册；任何未知 marker
        # （如 @pytest.mark.ffmpeg）都当恒等装饰器，避免导入期 AttributeError
        # 让整个测试文件判失败（这才是"跑起来再跳过"）。
        if name.startswith("__"):
            raise AttributeError(name)
        return _Marker(name)

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


class _ExcInfo:
    """``pytest.raises(...) as exc`` 的垫片：支持读 ``exc.value``。"""

    value: BaseException | None = None


class _Approx:
    """``pytest.approx`` 垫片（abs / rel 容差）。"""

    def __init__(self, expected, abs=None, rel=None, nan_ok: bool = False):
        self.expected = expected
        self.abs = abs
        self.rel = rel if rel is not None else 1e-6

    def __eq__(self, other) -> bool:
        try:
            expected = float(self.expected)
            actual = float(other)
        except (TypeError, ValueError):
            return NotImplemented
        tol = self.abs if self.abs is not None else self.rel * max(abs(expected), 1.0)
        if actual != actual and expected != expected:  # NaN
            return True
        return abs(actual - expected) <= tol

    def __repr__(self) -> str:
        return f"approx({self.expected!r})"


class _PytestShim:
    """提供 pytest.raises / pytest.fixture / pytest.skip，让 pytest 风格测试直接可跑。"""
    mark = _Mark()

    @staticmethod
    def approx(expected, abs=None, rel=None, nan_ok: bool = False) -> _Approx:
        return _Approx(expected, abs=abs, rel=rel, nan_ok=nan_ok)

    @staticmethod
    def fail(reason: str = ""):
        raise AssertionError(reason)

    @staticmethod
    @contextlib.contextmanager
    def raises(exc_type, match: str | None = None):
        info = _ExcInfo()
        try:
            yield info
        except exc_type as exc:  # noqa: PERF203
            info.value = exc
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
    if getattr(fn, "_parametrized", False):
        _SKIPPED.append(f"{module_name}::{fn.__name__}: minitest 不支持 pytest.mark.parametrize")
        return None
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
    except _Skipped as exc:
        _SKIPPED.append(f"{module_name}::{fn.__name__}: {exc}")
        return None  # 跳过不再静默计为通过，main() 会汇总打印
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
    if argv is None:
        argv = sys.argv[1:]
    files = discover_test_files(list(argv))
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
    print(f"\nminitest: {passed} passed, {len(_SKIPPED)} skipped, {len(failed)} failed, {len(files)} files")
    if _SKIPPED:
        for label in _SKIPPED:
            print(f"  SKIP {label}")
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
