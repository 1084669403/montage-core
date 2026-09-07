"""示例：一条完整的中文短片生产流（仅用本地确定性工具，无需 API 密钥）。

演示管线 API：init_project → checkpoint 状态机 → 词库检索 → 镜头提示词
→ 剪辑顾问 → 决策/成本记账。真实成片（供应商调用 + FFmpeg 合成）由你配好
密钥后按同样流程执行。

运行：python examples/pipeline_flow.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from montage.engine.artifacts import ArtifactStore
from montage.engine.budget import BudgetLedger
from montage.engine.decisions import DecisionLog
from montage.engine.project import init_project
from montage.engine.stages import CheckpointStore, StageStatus
from montage.schemas import get_schema
from montage.tools.edit_advisor import EditAdvisor
from montage.tools.prompt_retriever import PromptLibraryRetriever
from montage.tools.visual_prompt_builder import VisualPromptBuilder


def main() -> None:
    root = Path(__file__).resolve().parents[1]  # montage-core 根；init_project 会创建 projects/<id>
    project_id = "example-rainy-night"
    proj = init_project(root, project_id, "雨夜追凶（示例）", "cinematic")
    store = CheckpointStore(proj)
    artifacts = ArtifactStore(proj)
    ledger = BudgetLedger(proj / "cost.jsonl")
    decisions = DecisionLog(proj / "decisions.jsonl")

    # 1) 供应商锁定决策
    decisions.log(
        "video_loop", "供应商", "agnes",
        options_considered=["agnes", "jimeng", "doubao+edge"],
        rejected_because="Agnes 支持图+视频+配音闭环，画面一致性最好",
    )

    artifacts.write("research_brief", {
        "topic": "雨夜霓虹下的追逐短片",
        "audience": "短视频剧情观众",
        "style_direction": "冷色霓虹，手持不安",
    }, schema=get_schema("research_brief"))
    artifacts.write("proposal_packet", {
        "concept": "雨夜巷口一记转身",
        "video_loop": "agnes",
        "render_runtime": "ffmpeg",
        "output_profile": "youtube_landscape",
    }, schema=get_schema("proposal_packet"))

    # 2) 剧本（demo 数据）
    script = {
        "title": "雨夜追凶",
        "sections": [
            {"id": "sc01", "narration": "雨夜，霓虹下的城市街道。", "duration_seconds": 5},
            {"id": "sc02", "narration": "他转身，消失在巷口。", "duration_seconds": 5},
        ],
    }
    artifacts.write("script", script, schema=get_schema("script"))

    # 3) 分镜
    scene_plan = {
        "scenes": [
            {
                "id": "sc01",
                "description": "主角在雨夜霓虹街头驻足",
                "narrative_role": "establish_context",
                "hero_moment": False,
                "start_seconds": 0,
                "end_seconds": 5,
                "shot_language": {"shot_size": "wide", "camera_movement": "handheld", "lighting_key": "neon"},
            },
            {
                "id": "sc02",
                "description": "主角转身走进巷口",
                "narrative_role": "deliver_payload",
                "hero_moment": True,
                "start_seconds": 5,
                "end_seconds": 10,
                "shot_language": {"shot_size": "medium", "camera_movement": "dolly_in", "lighting_key": "low_key"},
            },
        ]
    }
    artifacts.write("scene_plan", scene_plan, schema=get_schema("scene_plan"))

    # 4) 词库检索参考（雨夜/霓虹）
    retriever = PromptLibraryRetriever()
    hits = retriever.execute({"query": "雨夜 霓虹 城市", "shot_kind": "image", "top_k": 3})
    print(f"[词库] 命中 {hits.data['count']} 条参考:",
          [h["title"] for h in hits.data["hits"]])

    # 5) 逐镜头双提示词（首帧 + 视频动态）
    builder = VisualPromptBuilder()
    shot = {
        "scene_id": "sc02",
        "shot_kind": "video",
        "duration_seconds": 5,
        "shot_language": scene_plan["scenes"][1]["shot_language"],
        "visual_details": {
            "environment": "雨夜霓虹巷口，积水倒映招牌光",
            "lighting": "低键光，霓虹冷色调",
            "cinematography": {"angle": "eye", "frame_composition": "主体居中"},
            "subjects": [{"id": "protagonist", "appearance_anchor": "黑发青年深灰卫衣",
                          "action": {"verb": "转身", "emotion": "决绝"}}],
        },
    }
    pair = builder.execute({"purpose": "shot", "shot": shot, "english_visual": True})
    print("[首帧提示词]", pair.data["first_frame_prompt"][:120], "...")
    print("[视频提示词]", pair.data["video_prompt"][:120], "...")

    # 6) 剪辑顾问（sc01→sc02 转场建议）
    advisor = EditAdvisor()
    junctions = advisor.execute({"scenes": scene_plan["scenes"], "style": "cinematic"})
    print("[剪辑顾问] 切点建议:", junctions.data["junctions"][0]["suggested_transition"])

    # 7) 门禁流程：research 完成 → script 等待审批 → 审批后完成
    # 示例直接写 checkpoint（演示状态机）。CLI `check --completed` 会先跑产物门禁。
    store.write("research", StageStatus.COMPLETED.value, human_approved=True)
    store.write("script", StageStatus.AWAITING_HUMAN.value, artifact="script.json")
    store.write("script", StageStatus.COMPLETED.value, human_approved=True, artifact="script.json")
    print(f"[门禁] 下一阶段: {store.next_stage()}")

    # 8) 成本记账
    eid = ledger.estimate("image_generation", "sc02 首帧", "agnes_image", 0.02)
    ledger.settle(eid, 0.019)
    print(f"[预算] 估算 ${ledger.totals()['estimated_usd']} 实付 ${ledger.totals()['settled_usd']}")

    print(f"\n完成。项目工作区: {proj}")
    print("下一步：真实成片走 `python -m montage run <project> compose_planner`（再 realize / place_audio / ffmpeg_compose assemble）；直接 import 不读 `.env`。")


if __name__ == "__main__":
    main()
