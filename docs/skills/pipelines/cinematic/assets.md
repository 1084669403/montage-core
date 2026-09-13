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
- **Agnes**：Phase A 全部静图（必须拿到公网 `url`）→ Phase B 视频。有人物时缺 URL **硬失败**，不降级文生视频。四视图/定妆进 `images[]`（最多 5 张，`mode=reference`），音频参考最多 3 段，**不改**【台词】【音频锁定】。默认 `agnes-video-2.5-flash`（`seconds` 字符串）；`AGNES_VIDEO_MODEL=agnes-video-v2.0` 才回滚 2.0。Flash 禁止 `videos[]`。单镜失败看 `retryable_ids`。时长建议 4–12s，超过 12s 切段后拼接。画幅按 `output_profile` 推导（`AGNES_RATIO` 可覆写，六值）。图片统一 `agnes-image-2.5-flash`（文生/编辑/多图合成；定妆参考支持本地路径，Data URI 装箱硬失败不降级；免费期图/视频计 0）。
  限流/配额：`AGNES_ACCESS_TYPE` 取 `default`/`enterprise`/`tokenplan`（**不设即免费档**）；视频实际 RPM 1/2/5，图片按 1K/2K/3K/4K 分档；Token Plan 另有每日 4000 张图 / 500 秒视频配额（`~/.montage/agnes_usage.json`，只告警不阻断）。
- **场记**：`artifacts/continuity.json` sidecar（不进 produces）。跨集续写读上一集场记，并拷贝兄弟集 portrait+turnaround。

空镜 `shot_kind=image` 走静图，assemble 前必须 ken_burns（`compose_planner realize=true`）。
道具参考按 `prop_id` 去重，全片最多 3 张，不按镜头张数翻倍。

## 角色形态 `forms[]` 与 `cast_ref_kind`（生成侧）

一个角色可有多个形态（如 `human` / `ghost`），每个形态一份定妆参考。**本期只改生成侧**：定妆/四视图按形态分别生成并按形态命名落盘；发送侧名额裁剪见后续计划。

- 人物卡内联在 `SCRIPT_SCHEMA.characters[]`（`SERIES_BIBLE_SCHEMA` 复用同一块），字段：
  - `forms: [{id, name, appearance, outfit_anchor, skip_turnaround, cast_ref_kind, default}]`
    - `id` 必填、角色内唯一；`name` / `appearance` / `outfit_anchor` 缺省回落角色同名字段（合并后供提示词使用）。
    - `default: true` 至多一个；若都不标，取第一个形态；`forms` 为空则视为单隐式形态（沿用旧行为）。
    - 形态数上限 `_MAX_FORMS`（`montage/tools/_shot_constants.py`），超限 bible 报 critical。
- `cast_ref_kind` 三级优先级：`form.cast_ref_kind` > `character.cast_ref_kind` > `proposal_packet.cast_ref_kind`，取值 `portrait`（默认）/ `turnaround`，由 `montage/engine/policy.py:resolve_cast_ref_kind` 归一。
- `skip_turnaround` 判定：`form.skip_turnaround` > `character.skip_turnaround`；为真则只出定妆不出四视图。
- **可灵环忽略 `forms[]`**：仍按单隐式形态出 `look_sheet`（一张拼板已含四视图）；bible 里声明了 `forms` 且 `video_loop=kling` 只给 warning。
- 落盘命名：无形态沿用 `portrait/<cid>`、`turnaround/<cid>`；有多形态时用 `<kind>/<cid>:<form>`，manifest `reference_assets.items[].form_id` 记形态。
- `--retry` 令牌同构：`portrait/<cid>:<form>`、`turnaround/<cid>:<form>`；无 form 的 `portrait/<cid>` 指该角色默认形态。
- `await_cast` 卡片按形态数估算生图张数，超 `_CAST_EST_IMAGE_WARN` 出 warning（不阻断）。
- 每镜出场形态写在 `scene_plan.scenes[].shots[].visual_details.subjects[].form_id`（bible 侧同名）；`character_registry[].forms` 逐字镜像人物卡。参考解析按声明形态取该形态定妆/四视图，未声明用默认形态；同镜同角色多形态会多占名额。`await_shots` 卡片「出场形态」列出各镜声明。
- **发送侧每个形态只发一张身份图**（默认 `portrait`，省下名额给 `scene_ref`/`prop`）：`turnaround` 需显式 `cast_ref_kind=turnaround`（`form` > `character` > `proposal_packet`）；可灵环强制等价 `turnaround`。短镜像图例用「角色·形态」并锁到该形态 `<Picture N>`；四视图行附「禁止分格/拼贴」。`await_setup`/`await_cast` 卡片展示当前 `cast_ref_kind`。
- **参考溢出默认镜内分段续拍**（`ref_overflow_mode=segment`，硬封顶 4 段）：同一 `shot_id` 沿时间轴切段，段间以「上段尾帧 + 本段参考」图片侧合成续接首帧作 `<Picture 1>`；段 2+ 因桥接位只剩 `上限−1` 个参考。`身份+场景` 超名额 / 时长切不动 / 超封顶 → 回退 `single`（丢弃 + finding），不会静默多花钱。`single` 回旧行为；`keyframe` 首帧模式强制 `single`。风险：段间可能有原生音频接缝；本段若含 `turnaround` 仍可能抄版面（图例已加单幅约束）。`dry_run` 按实际段数 + 续接帧数量估算。
- **图 ↔ 场景文字冻结绑定**（`artifacts/image_bindings.json`，**observe-only**，v1 不改 compose/edit 读取）：`shot_runner` 每次跑完（cast / shots 各一次）按 `shot_id` / cast `ref_id` **整条替换**合并写；`refs[].picture_index` 取**实发** `_agnes_ref_plan` 序号（`source=sent_plan`），分段时各段一条对象数组。produce 的 `shot_bind` 步非致命回填缺失镜、对账参考集合漂移（保留已有真序号，回填写 `source=recomputed` + `picture_index` 为空）。场景文字存 `location_sensory`（圣经裁切后的权威值）与 `visual_details.environment`。
