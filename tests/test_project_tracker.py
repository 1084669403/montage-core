"""V39/V46/V50 — 项目随行手册复制机制。

init_project / write_episode_skeleton 把 docs/PROJECT_TEMPLATE.md 复制为
PROGRESS_TRACKER.md + STATUS.md 骨架：缺才写（幂等）、显式 utf-8、模板缺省静默跳过。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from montage.engine.project import _TEMPLATE_DIR, init_project
from montage.engine.episodes import write_episode_skeleton


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_init_copies_both_files(tmp_path):
    proj = init_project(tmp_path, "v39a", "随行手册", "cinematic")
    tracker = proj / "PROGRESS_TRACKER.md"
    status = proj / "STATUS.md"
    assert tracker.is_file()
    assert status.is_file()
    template = _read(_TEMPLATE_DIR / "PROJECT_TEMPLATE.md")
    assert _read(tracker) == template  # 内容=模板全文
    assert "STATUS 手账" in _read(status)


def test_utf8_roundtrip_no_mojibake(tmp_path):
    proj = init_project(tmp_path, "v39b", "编码", "cinematic")
    text = _read(proj / "PROGRESS_TRACKER.md")
    # 中文与 mermaid 块原样保留，无 GBK 乱码迹象
    assert "流程图" in text
    assert "mermaid" in text
    assert "停点" in text


def test_second_init_does_not_overwrite(tmp_path):
    proj = init_project(tmp_path, "v39c", "幂等", "cinematic")
    tracker = proj / "PROGRESS_TRACKER.md"
    status = proj / "STATUS.md"
    tracker.write_text("AGENT 改过的手册行", encoding="utf-8")
    status.write_text("用户拍板：用可灵环", encoding="utf-8")
    # 再次 init（同目录重复初始化是合法操作）
    init_project(tmp_path, "v39c", "幂等", "cinematic")
    assert _read(tracker) == "AGENT 改过的手册行"
    assert _read(status) == "用户拍板：用可灵环"


def test_missing_template_skips_silently(tmp_path, monkeypatch):
    import montage.engine.project as project_mod

    monkeypatch.setattr(project_mod, "_TEMPLATE_DIR", tmp_path / "nonexistent_docs")
    proj = init_project(tmp_path, "v39d", "无模板", "cinematic")
    assert not (proj / "PROGRESS_TRACKER.md").exists()
    # STATUS 骨架不依赖模板，仍写
    assert (proj / "STATUS.md").is_file()


def test_episode_skeleton_idempotent_status(tmp_path):
    ep = tmp_path / "ep01"
    write_episode_skeleton(
        ep, parent_id="series", episode_id="ep01", title="第一集", pipeline_type="cinematic"
    )
    status = ep / "STATUS.md"
    status.write_text("集手账：本集复用 ep02 定妆", encoding="utf-8")
    # materialize 每轮反复调用——已改写内容不得被覆盖（V46）
    write_episode_skeleton(
        ep, parent_id="series", episode_id="ep01", title="第一集", pipeline_type="cinematic"
    )
    assert _read(status) == "集手账：本集复用 ep02 定妆"
    tracker = ep / "PROGRESS_TRACKER.md"
    tracker.write_text("集内手改", encoding="utf-8")
    write_episode_skeleton(
        ep, parent_id="series", episode_id="ep01", title="第一集", pipeline_type="cinematic"
    )
    assert _read(tracker) == "集内手改"
