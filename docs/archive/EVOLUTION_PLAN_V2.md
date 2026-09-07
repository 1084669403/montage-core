> **已被取代。** 现行方案见 [docs/EVOLUTION_PLAN.md](../EVOLUTION_PLAN.md)。本文仅作历史归档。

# montage-core 进化方案 V2：最少人工干预的电影级自动成片

> 本文档在两轮调研（① Pavo 剧情短片八步流程；② 可灵 / 即梦 Seedance / 海螺 / Vidu /
> Sora 2 / Veo 3 / 混元 / 万相 / VideoClaw / wind-comic / ArcReel / movieflow）基础上，
> 对 `docs/EVOLUTION_PLAN.md`（V1，P0–P5）做精细化：把"电影级"拆成可验收维度、
> 把各平台步骤归并成统一 SOP、把改动落到**真实文件与字段**。
> 目标：**一句话创意 → 电影级成片，人工干预降到 1~2 次关键确认 + 异常兜底**。
>
> 许可证红线不变：MIT，只借鉴"功能清单 / 接口契约 / 设计思路"，代码 100% 自创；
> ArcReel 为 AGPL、OpenMontage 为 AGPL，剪映草稿导出等一律自写实现。

---

## 0. 目标定义（可验收）

### 0.1 "电影级"的 6 个验收维度

| # | 维度 | 对标来源 | 判据（一句话） |
|---|------|----------|----------------|
| C1 | 角色/场景/道具**跨镜头一致** | 混元"不变脸不漂移"、可灵多图参考、Vidu 多主体参考 | 同一角色跨 ≥5 镜，外观/服装/道具不漂移 |
| C2 | 镜头语言/运镜**贴合情绪节拍** | 海螺 Director、即梦多镜头叙事 | 每个镜头运镜可解释（节拍→运镜映射），构图无遮挡/出画 |
| C3 | 光影/色调**统一**（电影感） | movieflow 后期、montage 已有多 LUT | 跨镜头白平衡/色调一致，整片一次调色 |
| C4 | 音画/对白/**口型** | Seedance 2.0、Kling 3.0、Veo 3 原生音频、Sora 2 唇形 | 对白镜不是"旁白声+无口型" |
| C5 | 节奏/转场/剪辑 | wind-comic 编辑器、montage 已有多 xfade | 转场与情绪匹配，无突兀硬切（除纪实管线） |
| C6 | 无硬伤（坏帧/崩坏/语义漂移） | VideoClaw VLM 闭环 | 坏帧/漂移被质检层拦截并触发重生成 |

### 0.2 "少人工"的度量

- **现状**：5 个门禁阶段（`proposal/script/scene_plan/assets/publish`）写 `completed`
  需要 `human_approved` 或 `approved_by∈{human,produce}`（`montage/engine/stages.py:_gate_allows`）。
  目前 `approved_by=produce` 只是"机器收口通道"，但**没有自动审批器去算它**。
- **目标**：确定性校验全绿 → 机器收口（`approved_by=produce`，**永不伪造 human_approved**）；
  有 critical → 才升级人工（`awaiting_human` 携带诊断报告）。人工从"每步都点"
  降为"只处理机器拿不准的异常 + 1~2 次关键确认（提案/样片）"。

---

## 1. 平台详细制作步骤 → 统一 SOP（10 环节）

> 把 Pavo 八步 + 各平台关键步骤归并成"电影级 AI 短剧 SOP"，逐环节对照 montage-core 现状。

| # | 环节 | 对标平台/步骤 | montage-core 现状 | 差距等级 |
|---|------|---------------|-------------------|----------|
| 1 | 一句话创意 → 需求卡/bible | Pavo 步骤 1–2；movieflow L0 提示词验证 | `idea_developer(skeleton/validate/compile)`，**无 LLM，bible 需 Agent 手写** | **P0** |
| 2 | 人设/场景/道具（多主体一致性） | Pavo 步骤 3；Vidu Q3 多主体参考；混元不漂移 | `characters[]` + `character_registry` + `reference_assets[]`（定妆照）；**缺四视图与多主体组** | **P1** |
| 3 | 剧本（人物卡/对白预算/四拍结构） | Pavo 步骤 2；montage DIRECTOR_GUIDE | `script` schema + `script_validator` 已完整 | ✅ 已强 |
| 4 | 分镜 + 镜头语言 | Pavo 步骤 4；海螺 Director（镜头语言一等公民）；即梦多镜头 | `scene_plan` + `shot_language` + `edit_advisor`（提示词层） | **P3** |
| 5 | 关键帧/首帧构图锁定 | Pavo 步骤 5；wind-comic sketch-lock | `shot_runner` 首帧 + `apply_video_frames` | **P3**（sketch-lock） |
| 6 | 视频生成（图生视频/多镜头/首尾帧桥） | Pavo 步骤 6；可灵/Seedance **一场多镜**；Vidu R2V；即梦首尾帧 | 逐镜生成 + FFmpeg 拼接；即梦首尾帧桥已有；**无一场多镜、无多主体参考** | **P2** |
| 7 | 原生音频/对白/口型 | Pavo 步骤 7；Seedance/Kling/Veo3 原生音频；Sora2 唇形 | TTS + ducking/loudnorm；`lip_sync` **待联调**；Agnes 闭环走片内音 | **P4** |
| 8 | 质检/场记/VLM 闭环 | VideoClaw；montage 确定性质检 | `asset_quality_gate`（确定性）+ `asset_picker`（A/B）；**缺 VLM 语义质检 + 场记库** | **P1** |
| 9 | 合成/调色/字幕/剪辑 | Pavo 步骤 8；movieflow 成本矩阵 | `compose` + `publish` + LUT + 平台档案 + 字幕 | ✅ 已强 |
| 10 | 返工/续写/交付 | Pavo 镜头级返工；wind-comic 分段重拍；VideoClaw 续写；ArcReel 剪映草稿 | `--retry sh01` + `generation_cache`；**缺分段重拍、剪映草稿、跨集场记续写** | **P2/P5** |

**核心判断（决定软件价值上移到哪里）**：能力层（环节 2/6/7）正被头部模型"原生能力"快速吞掉
——可灵/Seedance 一场多镜、Vidu 多主体参考、Seedance/Veo 原生音频。montage-core 若继续
只靠"逐镜生成 + 拼贴 + 后叠 TTS"，会逐渐追不上手搓成片。**软件层唯一不可被模型替代的价值 =
治理 / 可审计 / 跨供应商编排 / 成本 / 返工 / 确定性兜底**，必须把这些做成增量护城河，而不是
去复刻模型已经原生具备的能力。

---

## 2. 分层进化方案（精细版）

> 分两层推进，每层独立可验证、可回滚。
> - **层 A（少人工）**：P0 — 让"一句话出片"跑通、机器能自己收口。
> - **层 B（电影级）**：P1–P5 — 让成片质量追平手搓。

---

### P0 — 少人工骨架 + 能力表先行（第一优先）

**目标**：`python -m montage produce <dir> --idea "一句创意" --autopilot full` 一条命令，
在确定性校验全绿时无人工跑通 `proposal→…→publish`；任何 critical 都停下并给出可读 `proposed_fix`。

**改动清单（落到文件/字段）**：

1. **能力表先行（纯数据，零风险）** — `montage/providers/capabilities.py`
   - `VIDEO_BY_TOOL` 每项增补字段（缺省继承 `_NO_VIDEO` 的 False/空值）：
     `multi_shot`（是否一场多镜）、`multi_shot_max`（一场最大镜头数）、
     `native_audio`（是否原生音频）、`lipsync`（是否口型）、
     `camera_control`（是否原生吃运镜/构图指令）、`max_duration`（单条最大秒数）、
     `negative_prompt`（是否支持负向）、`multi_subject_refs`（多主体参考图组）。
   - `IMAGE_BY_TOOL` 增补：`turnaround`（是否支持四视图/多视图定妆）。
   - 现状确认：当前只有 `image_reference/first_frame/last_frame/mode/duration_policy`，
     即梦 `i2v_first_tail`、Agnes `keyframe_chain` 已是最强的两个。
2. **proposal 契约扩展** — `montage/schemas.py:PROPOSAL_PACKET_SCHEMA` 增补字段：
   - `autopilot`（`enum: ["off","gated","full"]`）
   - `human_checkpoints`（`array<string>`，只在这些阶段强制等人，如 `["proposal","script"]`）
   - `dialogue_audio_mode`（`enum: ["native","tts","lipsync"]`，P4 消费，P0 先入 schema）
   - `gen_strategy`（`enum: ["shot_by_shot","single_call_multi_shot"]`，P2 消费，P0 先入 schema）
   - `race_providers`（`array<string>`）、`race_budget_factor`（`number`，P2 消费）
3. **自动审批器** — 新增 `montage/engine/autopilot.py`：
   - `review_stage(project_dir, stage) -> {pass, findings[], needs_human}`：
     按阶段调度现有确定性校验（`script_validator` / `edit_advisor` / `asset_quality_gate`）+ 决策日志。
   - `auto_approve(project_dir, stage)`：`pass` 则 `CheckpointStore.write(stage, "completed",
     approved_by="produce", note="autopilot: <findings 摘要>")`；否则写 `awaiting_human`，
     findings 写进 `decision_log category=review`。
   - **铁律**：不伪造 `human_approved`；`stages.py:_gate_allows` 不改（机器收口仍走 `produce`）。
4. **idea→bible 自动生成** — `montage/tools/idea_developer.py` 新增 `operation=autowrite`：
   - 在现有 `skeleton/validate/compile`（确定性、无 LLM）之上，允许用一句话让 LLM
     生成 `series_bible` 草稿（脚本层由导演 Agent 补，代码仍只做确定性转换与校验）。
   - 把 `--idea` 从"只收编圣经"推进到"idea→bible→script→scene_plan 全自动草稿"。
5. **produce 编排接入** — `montage/engine/produce.py`：
   - 在 `GEN_STEP_IDS` 前插入 `autopilot` 步骤，依次对 gated 阶段跑 `auto_approve`；
     `--autopilot full` 全部机器收口，`--autopilot gated` 按 `human_checkpoints` 保留人审。
   - `run_idea_produce` 的 `cascade` 步改走 `autowrite`（可选），`--review none` 语义不变。

**验收**：`--autopilot full` 在确定性校验全绿时零人工跑通；任一 critical 停在
`awaiting_human` 并带 `proposed_fix`。`doctor --json` 能列出新增能力字段（供选型器读）。

---

### P1 — 电影级一致性闭环（命门，对齐混元/Vidu/可灵）

**目标**：角色/场景/道具跨镜头不漂移，坏素材被 VLM 语义层拦截。

**改动清单**：

1. **四视图角色卡 + 多主体参考组**（超 V1 的"单四视图"，对齐 Vidu Q3 R2V）：
   - `montage/schemas.py:SCRIPT_SCHEMA.characters.items` 增补
     `turnarounds`（`array<string>`，`["front","side","back","three_quarter"]`）。
   - `ASSET_MANIFEST_SCHEMA.reference_assets` 的 `kind` 增补 `turnaround`；
     `shot_runner` 生成定妆照时按 `turnarounds` 产出多视图，写入 `reference_assets`
     （同 `character_id` 多张）。
   - `capabilities.apply_image_refs` 支持**多 URL 一次性注入**（角色四视图 + 场景 + 道具
     同一 `reference_assets[]` 组），形成"多主体参考组"。
2. **场记库（continuity）** — 新增 `montage/engine/continuity.py` + `artifacts/continuity.json`
   （sidecar，不进 pipeline produces，同 `series_bible`）：
   - 字段：`characters[]`（当前位置/服装状态/手持道具）、`props[]`（在场/缺失）、
     `locations[]`、`last_shot_id`。
   - `shot_runner` 每镜生成前把"上一镜场记摘要"注入提示词（走
     `visual_prompt_builder` 之外的独立通道，不污染 `style_context`）。
   - `montage/engine/episodes.py` 续写时读上一集 `continuity.json` → 跨集一致。
3. **VLM 语义质检** — 新增 `montage/tools/vlm_reviewer.py`（`capability="analysis"`，
   provider 优先 `dashscope` 通义千问 VL，复用 `DASHSCOPE_API_KEY`；留 OpenAI-compatible 接口）：
   - 输入 `{media_path, mode: first_frame|video_clip, shot, character_registry, script, expected}`；
     输出 `{ok, score, issues[]}`，issue 含 `severity/kind(人物不一致|道具丢失|场景错位|崩坏|构图)/message/proposed_fix`。
   - `shot_runner` 的质检钩子后追加 VLM 审查：确定性 gate 过 → VLM 过 → 收进 manifest；
     VLM critical → 计入 `retryable_ids` 换 seed 重生成。
   - `montage/pipelines.py` 的 `_ASSETS_CORE` 加 `"vlm_reviewer"`。
4. **门禁升级** — `montage/engine/gates.py:_completeness_blockers` 扩展为
   "确定性 critical + VLM critical 均挡门"（VLM 未配密钥时降级 warning，不阻塞，向后兼容）。

**验收**：同一角色跨 ≥5 镜，VLM 复查"人物漂移"命中率显著下降；能揪出"定妆黑发、镜头金发"
这类语义不一致并触发重生成。

---

### P2 — 生成策略升级（竞赛 / 中间提示词 / 分段重拍 / 一场多镜）

**目标**：单镜质量追平头部长视频模型，返工只重拍坏段。

**改动清单**：

1. **多引擎竞赛（race）** — `montage/providers/selectors.py` 加 `race` 模式：
   - `VideoSelector` 新增 `race_providers`（读 `proposal_packet.race_providers`，提案
     `allowed_providers` 内多供应商）；同镜生成 N 份 → `asset_picker`（确定性）+ VLM
     （语义）双评分，首个过阈值或最高分胜出；失败镜头标 `animatic`，不拿静图冒充。
2. **平台无关中间提示词** — 新增 `montage/providers/prompt_adapter.py`：
   - 定义 `StandardVisualPrompt`（foreground/midground/background/camera/shot_size/
     dialogue/emotion），按 `capabilities.py` 表转成即梦/Agnes/万相/清影方言。
   - `visual_prompt_builder` 不动，`prompt_adapter` 在其下游消费
     `first_frame_prompt/video_prompt` 做方言化（复刻 movieflow 思路）。
3. **分段重拍（对齐 wind-comic）** — `montage/compose/effects.py` 加
   `operation=retake_segment`（或 `ffmpeg_compose` 新操作）：
   - `-ss/-to` 定位坏段重生成，未改段 `-c copy` 字节拷贝，时长不变 → 时间线/字幕/EDL 免重算。
   - 新增 sidecar `artifacts/rework_requests.json`：`[{shot_id, segment, revision_note, regenerate}]`，
     `shot_runner` 只重跑清单内镜头（复用 `retry_ids` + `generation_cache`）。
4. **一场多镜（新范式，P0 已入 schema `gen_strategy`）**：
   - `policy.py` + `shot_runner` 选型时：若供应商 `multi_shot=True` 且一场内多镜
     `location_id` 相同、`cut=bridge`，允许把 N 个连续镜头合并成**一次单 call 多镜生成**，
     减少拼接与一致性问题；否则仍走逐镜生成 + FFmpeg 拼接。
   - **保留逐镜路径作为兜底**：它才是能审计/返工/控成本的路径。
5. **长镜头优先** — `capabilities.py` 的 `max_duration/native_audio/lipsync/negative_prompt`
   落地：选型优先 `max_duration ≥ 镜头时长` 且 `native_audio` 的供应商（对齐可灵/Seedance/Sora2）。
6. **镜头分层成本执法** — `shot_budget_class`（hero/talk/establishing）P2 落地：
   hero 走高质量模型，establishing 走静图+Ken Burns 或低成本模型，dry_run 按 class 分档计费。

**验收**：同镜可配置双供应商竞赛；坏段重拍不重编码好段；长镜头不再被无谓切碎；
一场多镜供应商可用时，单 call 多镜路径跑通且可回退逐镜。

---

### P3 — 导演语言层（节拍→运镜 / 构图审计 / sketch-lock）

**目标**：镜头语言自动贴合情绪节拍，生成前拦截构图硬伤（对齐海螺 Director、wind-comic）。

**改动清单**：

1. **节拍→运镜映射** — 新增 `montage/director.py`（确定性 dict）：
   `beat_to_camera = {hook: push_in, escalation: handheld, reveal: zoom_in, landing: pull_out,
   chase: handheld, farewell: pull_out}`。
   - `edit_advisor.py` 与 `visual_prompt_builder` 消费：镜头缺 `shot_language.camera_movement`
     时按 `narrative_role` 推导补写。
   - 若供应商 `camera_control=True`，走原生运镜参数（对齐海螺 Director）；否则走提示词层。
2. **构图审计（生成前）** — `script_validator.py` 新增 `purpose=composition`：
   - 基于 `blocking.x/z`（left/center/right × near/mid/far）+ `shot_size`，确定性检查
     "同场两角色 blocking 重叠→遮挡风险"、"特写却给 far 站位→出画风险"，输出 `proposed_fix`。
3. **节拍覆盖校验** — `script_validator.py` 新增 `purpose=beat_coverage`：
   `scenes[].narrative_role` 是否覆盖 `structure.{hook,escalation,reveal,landing}` 四拍，
   时长占比（≈15%/50%/75%/20%）异常即 warning。
4. **sketch-lock 骨架锁定** — `shot_runner` 加 `sketch_lock`（proposal 透传）：
   首帧前先出低分辨率构图草图，终版生成锁该构图（规避"镜头语言被扩散模型抽奖"）。

**验收**：无运镜镜头自动获得节拍驱动运镜；`purpose=composition` 生成前报遮挡/出画；四拍缺失即 warning。

---

### P4 — 音画与后期电影感（对白策略 / 原生音频 / 健康门禁 / 电影感）

**目标**：对白不出戏，成片无技术硬伤，色调统一（对齐 Seedance/Kling/Veo3 原生音频）。

**改动清单**：

1. **对白镜音频策略** — 消费 P0 已入 schema 的 `dialogue_audio_mode(native|tts|lipsync)`：
   - `shot_runner`/`voice_director`：对白优先镜头（`audio_prompt.dialogue` 非空）
     优先走 `native_audio` 供应商（Agnes 片内音、可灵原生音频），否则 TTS + `lip_sync`
     （补齐 `talking_head`/`lip_sync` 联调）。
   - Agnes 闭环不 TTS 的现有语义保留；非对白镜仍走 TTS 旁白 + ducking/loudnorm。
2. **发布健康门禁** — 新增 `montage/tools/film_health.py`（`capability=analysis`）：
   - publish 前 ffprobe 全片（分辨率/时长/fps/码率/音轨/劣化镜头聚合），产出 `film_health.json`；
   - `pipelines.py` publish 阶段 `produces` 加 `film_health`（或 sidecar 检查），critical 挡 `export_bundle`。
3. **电影感后期** — 复用 `apply_lut`（`lut_strength`）+ `cinematic_21_9` 裁切 +
   `compose/effects.py` 加 `grain` 滤镜（无新依赖）。

**验收**：对白镜不再"旁白声+无口型"；发布前自动报告全片健康；色调跨镜头一致。

---

### P5 — WebUI 导演控制台 + 交付生态

**目标**：把机器能力变成"导演可看、可勾选返工"的界面（对齐 ArcReel、wind-comic）。

**改动清单**：

1. **WebUI**（`montage/webui/`）：
   - 分镜 blocking 俯视图（读 `blocking.x/z` 渲染 stage 图）；
   - 逐帧审查面板（接 `frame_sampler` + `vlm_reviewer`，框选坏段 → 生成 `rework_requests`）；
   - 阶段进度 + 机器收口/人工待审状态可视化（读 `checkpoint_*.json`）。
2. **剪映草稿导出** — `export_bundle` 加剪映草稿格式（**自创实现**，参考 ArcReel 仅借功能清单）。
3. **模板市场** — 成功项目 → 可复用模板（`format_card` + `bible` + `proposal` 打包）。

**验收**：普通创作者能在 WebUI 完成"确认提案 → 审查分镜 → 勾选返工 → 出片"。

---

## 3. 优先级矩阵 + MVP

| 阶段 | 影响 | 成本 | 风险 | 建议顺序 |
|------|------|------|------|----------|
| P0 少人工 + 能力表 | 高（直接减人工） | 中 | 低（复用 produce/checkpoint） | **第一** |
| P1 一致性闭环 | 最高（电影级命门） | 高（VLM 接入） | 中（VLM 成本/稳定性） | **第二** |
| P2 生成策略 | 高（追平头部模型） | 高 | 中（供应商联调） | 第三 |
| P3 导演语言 | 中高（镜头质感） | 低（确定性规则） | 低 | 可与 P0 并行 |
| P4 音画后期 | 中（对白/健康） | 中 | 中（lip_sync 依赖） | 第四 |
| P5 体验交付 | 中（易用性） | 中高 | 低 | 最后 |

**最小可行闭环（MVP，先做这三件，即可从"手搓管线"跨到"半自动电影级"）**：
1. **P0 能力表 + Autopilot**（`--autopilot full` 一条命令跑通，人工兜底）；
2. **P1 的 VLM 质检 + 场记库**（坏素材/漂移拦截，电影级命门）；
3. **P3 节拍→运镜 + 构图审计**（纯确定性、零新依赖、立竿见影）。

---

## 4. 风险与降级（保持向后兼容）

- **VLM 不可用/未配密钥**：`vlm_reviewer` 状态 `NEEDS_CONFIG`，质检降级为
  `asset_quality_gate` 确定性通道，不阻塞（同 `MONTAGE_RELAX_GATES=1` 语义）。
- **机器收口误判**：Autopilot 只对"确定性全绿 + VLM 无 critical"收口；任何 critical
  一律 `awaiting_human`，全程写 `decision_log category=review` 可回溯。
- **供应商能力差异**：全部走 `capabilities.py` 能力表降级，不按供应商名猜参数；
  新字段缺省 False/空值，老供应商行为不变。
- **一场多镜失败**：`gen_strategy=single_call_multi_shot` 仅在 `multi_shot=True` 时启用，
  失败自动回退逐镜生成 + FFmpeg 拼接。
- **成本失控**：Autopilot 不改变 `budget_ceiling_usd` 硬停 + dry_run 估算语义；
  race 模式用 `race_budget_factor` 封顶。
- **许可证**：新增代码全部自创；ArcReel（AGPL）/OpenMontage（AGPL）只借功能清单，不复制实现。

---

## 5. 一句话总结

- **P0 给"少人工"装上开关**：能力表先行 + Autopilot 机器收口 + idea→bible 自动生成。
- **P1 给"电影级"装上命门**：四视图 + 多主体参考组 + 场记库 + VLM 语义质检。
- **P2 给"追平头部"装上引擎**：竞赛 + 中间提示词 + 分段重拍 + 一场多镜 + 长镜头优先。
- **P3 给"镜头质感"装上导演**：节拍→运镜 + 构图审计 + sketch-lock。
- **P4/P5 给"成品"装上专业后期与交付**：原生音频/口型 + 健康门禁 + 剪映草稿。

现有资产（预算账本、确定性门禁、镜头分层成本、多供应商显式路由、可审计、170 条中文词库 +
7 playbook）是开源同类里最扎实的一档；本方案在其上做增量，不推翻、不破坏 MIT 边界。
