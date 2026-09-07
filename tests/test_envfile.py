"""仓库根 .env 加载：空值跳过、不覆盖、example 覆盖全部 env_keys。"""

from pathlib import Path

from montage.engine.envfile import (
    apply_env,
    collect_tool_env_keys,
    ensure_dotenv,
    find_repo_root,
    injected_key_count,
    injected_key_names,
    listed_env_names,
    load_envfile,
    parse_env_text,
)
from montage.registry import ToolRegistry

ROOT = Path(__file__).resolve().parents[1]


def test_parse_skips_empty_and_comments():
    text = """
# VOLC_ACCESSKEY=hidden
VOLC_ACCESSKEY=
DASHSCOPE_API_KEY=abc
export ZHIPU_API_KEY="k2"
DOUBAO_SPEECH_API_KEY='k3'
not a line
"""
    parsed = parse_env_text("\ufeff" + text)
    assert "VOLC_ACCESSKEY" not in parsed
    assert parsed["DASHSCOPE_API_KEY"] == "abc"
    assert parsed["ZHIPU_API_KEY"] == "k2"
    assert parsed["DOUBAO_SPEECH_API_KEY"] == "k3"


def test_apply_does_not_override_existing():
    env = {"DASHSCOPE_API_KEY": "keep"}
    applied = apply_env({"DASHSCOPE_API_KEY": "new", "ZHIPU_API_KEY": "z"}, env)
    assert env["DASHSCOPE_API_KEY"] == "keep"
    assert env["ZHIPU_API_KEY"] == "z"
    assert applied == ["ZHIPU_API_KEY"]


def test_load_skips_empty_file_values(tmp_path, monkeypatch):
    monkeypatch.delenv("MONTAGE_SKIP_DOTENV", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    (tmp_path / "pyproject.toml").write_text('name = "montage-core"\n', encoding="utf-8")
    (tmp_path / ".env").write_text("VOLC_ACCESSKEY=\nVOLC_SECRETKEY=sk\n", encoding="utf-8")
    env: dict[str, str] = {}
    applied = load_envfile(root=tmp_path, environ=env, force=True)
    assert "VOLC_ACCESSKEY" not in env
    assert env["VOLC_SECRETKEY"] == "sk"
    assert applied == ["VOLC_SECRETKEY"]


def test_skip_flag(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text('name = "montage-core"\n', encoding="utf-8")
    (tmp_path / ".env").write_text("ZHIPU_API_KEY=should-not-load\n", encoding="utf-8")
    env = {"MONTAGE_SKIP_DOTENV": "1"}
    assert load_envfile(root=tmp_path, environ=env) == []
    assert "ZHIPU_API_KEY" not in env


def test_ensure_dotenv_copies_once(tmp_path):
    example = tmp_path / ".env.example"
    example.write_text("VOLC_ACCESSKEY=\n", encoding="utf-8")
    dest = ensure_dotenv(tmp_path)
    assert dest is not None and dest.exists()
    dest.write_text("VOLC_ACCESSKEY=keep\n", encoding="utf-8")
    ensure_dotenv(tmp_path)
    assert dest.read_text(encoding="utf-8") == "VOLC_ACCESSKEY=keep\n"


def test_find_repo_root_from_nested(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "montage-core"\n', encoding="utf-8")
    nested = tmp_path / "projects" / "demo"
    nested.mkdir(parents=True)
    assert find_repo_root(nested) == tmp_path.resolve()


def test_example_lists_all_tool_env_keys():
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    listed = listed_env_names(example)
    reg = ToolRegistry()
    reg.discover()
    needed = collect_tool_env_keys(cls() for cls in reg._tools.values())
    needed.update({
        "AGNES_API_BASE_URL",
        "DOUBAO_SPEECH_VOICE_TYPE",
        "PIXABAY_API_KEY",
        "PEXELS_API_KEY",
        "MONTAGE_REAL_FFMPEG",
        "MONTAGE_BUDGET_SOFT",
        "MONTAGE_RELAX_GATES",
        "MONTAGE_HEADLESS",
    })
    missing = sorted(needed - listed)
    assert not missing, f".env.example 缺少: {missing}"


def test_injected_count_survives_second_load(tmp_path, monkeypatch):
    import montage.engine.envfile as envfile

    monkeypatch.setattr(envfile, "_loaded", False)
    monkeypatch.setattr(envfile, "_last_applied", [])
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    monkeypatch.delenv("MONTAGE_SKIP_DOTENV", raising=False)
    (tmp_path / ".env").write_text("ZHIPU_API_KEY=fromfile\n", encoding="utf-8")
    applied = envfile.load_envfile(root=tmp_path, force=True)
    assert applied == ["ZHIPU_API_KEY"]
    assert injected_key_count() == 1
    envfile._loaded = True
    assert envfile.load_envfile(root=tmp_path) == []
    assert injected_key_count() == 1
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)


def test_custom_environ_does_not_mutate_last_applied(tmp_path, monkeypatch):
    import montage.engine.envfile as envfile

    monkeypatch.setattr(envfile, "_loaded", False)
    monkeypatch.setattr(envfile, "_last_applied", ["KEEP"])
    (tmp_path / ".env").write_text("ZHIPU_API_KEY=x\n", encoding="utf-8")
    env: dict[str, str] = {}
    applied = envfile.load_envfile(root=tmp_path, environ=env, force=True)
    assert applied == ["ZHIPU_API_KEY"]
    assert envfile.injected_key_names() == ["KEEP"]


def test_repo_root_is_this_checkout():
    found = find_repo_root(ROOT / "montage" / "cli.py")
    assert found == ROOT
