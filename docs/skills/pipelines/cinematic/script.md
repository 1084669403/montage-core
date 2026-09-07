# cinematic / script

产出：`artifacts/script.json`（`title` + `sections[]`；可选 `environment` / `props` / `tone` / `characters` / `structure` / `sections[].lines[]`）。

| 槽 | 谁写 | 校验 |
|----|------|------|
| `environment`（地点/空间/光线/色调/时代/氛围） | 导演 | `script_validator purpose=completeness`；有 playbook 且 `require_environment` 才 critical |
| `characters[]`（id/appearance/outfit） | 导演 | 叙事片 warning；playbook `require_characters` 才 critical |
| `structure` 四拍 hook/escalation/reveal/landing | 导演 | `require_structure` 时缺 hook 为 critical |
| `sections[].lines[].speaker_id` + `text` | 导演 | `lines[]` 是对白真相；`narration` 旁白。`require_speakers` 才 critical |
| `props[]` | 导演 | 可选；转换器会抄进每镜 `visual_details.objects`（全量，精修可删） |
| `proposal.playbook` | proposal 阶段 | **不要**在 script 上再写 style_binding。无 playbook 时 completeness 永不挡 completed |
| 模板 | `prompt_library_retriever` | `scripts/elements-cinematic`（别名 film）；动漫/漫画/口播见 `elements-anime/manga/spoken` |

先检索结构词条与要素模板，再写原创剧本。即梦闭环时对白按时长网格（5/10 秒）。

R0 样片：proposal 必须带 `playbook` + `video_loop`（按 `montage doctor` 里 available 的图/视频选 `volcengine` 或 `dashscope`）+ `budget_ceiling_usd`。

门禁：`--completed --approved`。
