# cinematic / assets

产出：`asset_manifest` + `shot_prompts`。

| 槽 | 谁写 | 校验 |
|----|------|------|
| 成片片段 | `shot_runner` → `items[]` | `kind=video\|image\|audio…`；拼片只认这里的 path |
| 定妆/场景/道具参考 | `shot_runner` → `reference_assets[]` | `kind=portrait\|turnaround\|scene_ref\|style_anchor\|prop`（**不要**写进 items.kind）。四视图是一张转面图，不是 4 次生图 |
| 预算 | `proposal.budget_ceiling_usd` | 先 `dry_run=true` 估成本；超封顶不打 API |
| 图/视频环 | `proposal.video_loop` | `volcengine` / `ark`（Seedance 2.5）/ `dashscope` / `agnes` / `kling`；按 doctor 里 available 与「视频 API 面」选。**不要**以为配了方舟 Key 就会替换 volcengine |
| 配音 | `voice_director` | 默认不合成；`synthesize=true` 才 TTS。不受 `allowed_providers=["volcengine"]` 约束。数字人 `talking_head` 待联调，口播走 TTS。**Agnes 闭环不要 TTS**：片内音 = 成片声音 |
| 尾帧 | 能力表 | 即梦 `i2v_first_tail`（抽成片末帧）；Agnes Flash **不抽尾帧**、不用 `videos[]`，定妆图走 `images[]` |

先跑 `shot_runner`（**默认 `dry_run=true`**）。`montage produce` 在 cinematic/documentary 且成片未齐时会先 dry_run，默认只生成样品镜后停；检查后 `--resume`。确认预算后再让 produce 往下走。Skill 不要裸调 `shot_runner` 代替 produce。

- **即梦 / Seedance**：逐镜 定妆照 → 首帧（带参考图，按能力表降级）→ 质检 + 可选千问 VLM → 图生视频。无定妆禁止 I2V。方舟有首尾帧时不再附带 `reference_image`（官方互斥）；身份锁在首帧。
- **可灵 Omni**：`image_list` 含首帧 + 定妆/四视图 `type=subject`。
- **Agnes**：Phase A 全部静图（必须拿到公网 `url`）→ Phase B 视频。有人物时缺 URL **硬失败**，不降级文生视频。四视图/定妆进 `images[]`（最多 5 张，`mode=reference`），**不改**【台词】【音频锁定】。默认 `agnes-video-2.5-flash`（`seconds` 字符串，约 1 RPM）；`AGNES_VIDEO_MODEL=agnes-video-v2.0` 才回滚 2.0。Flash 禁止 `videos[]`。单镜失败看 `retryable_ids`。时长建议 4–12s，超过 12s 切段后拼接。
- **场记**：`artifacts/continuity.json` sidecar（不进 produces）。跨集续写读上一集场记，并拷贝兄弟集 portrait+turnaround。

空镜 `shot_kind=image` 走静图，assemble 前必须 ken_burns（`compose_planner realize=true`）。
道具参考按 `prop_id` 去重，全片最多 3 张，不按镜头张数翻倍。
