# cinematic / scene_plan

产出：`artifacts/scene_plan.json`（`scenes[]`）。本阶段 **不要** 写 `shot_prompts.json`。

| 槽 | 谁写 | 校验 |
|----|------|------|
| `scenes[]` 骨架 | `script_to_scene_plan` | 1 section = 1 scene + N nested shots；每场最多 `_MAX_SHOTS=4`（长对白会合并，导演可精修拆开） |
| `scenes[].shots[]` | 转换器 + 导演精修 | 形状对齐 `visual_prompt_builder` 的 shot：`visual_details` / `audio_prompt` / `shot_language` |
| `character_registry` | 转换器 | 从 `script.characters` **逐字**复制 appearance/outfit，不要改写 |
| 已有精修 | — | 默认不覆盖，除非 `overwrite=true` |
| 转场建议 | `edit_advisor` | 只是建议；最终进 compose 的 `edit_decisions` |

写完跑 `script_validator`（`purpose=shot_completeness` / `character_refs` / `composition` / `beat_coverage`）。无 playbook 时 shot_completeness 不挡 completed。缺运镜时 compile 会按 `narrative_role` 补 `shot_language`（显式值不覆盖）。
