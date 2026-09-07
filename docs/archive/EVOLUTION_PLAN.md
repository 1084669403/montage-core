> **已被取代。** 现行方案见 [docs/EVOLUTION_PLAN.md](../EVOLUTION_PLAN.md)。本文仅作历史归档。

# montage-core 进化方案：最少人工干预的电影级自动成片

> 目标：把 montage-core 从"治理完整的 7 阶段 AI 视频管线"升级为
> **一句话创意 → 电影级成片，人工干预降到最低（1~2 次关键确认 + 异常兜底）**。
> 本方案基于 Pavo / 可灵 / 即梦 / VideoClaw / wind-comic / ArcReel / movieflow
> 的制作流程拆解，逐条落到本项目**真实文件与字段**，不引入 Remotion/HyperFrames，
> 许可证保持 MIT（只借鉴功能清单，代码自创）。

---

## 0. 目标拆解：什么算"电影级 + 少人工"

### 0.1 "电影级"的 6 个可验收维度（对齐可灵/即梦卖点）

| # | 维度 | 对标来源 | montage-core 现状 |
|---|------|----------|-------------------|
| C1 | 角色/场景/道具**跨镜头一致** | 可灵角色一致性、Pavo 素材引用 | 已有定妆照 + character_registry 逐字复制 + reference_assets；缺四视图与场记库 |
| C2 | 镜头语言/运镜**符合情绪与节拍** | 即梦多镜头叙事、wind-comic 情绪运镜 | 已有 shot_language + edit_advisor；缺"节拍→运镜"自动映射与构图审计 |
| C3 | 光影/色调**统一**（电影感调色） | movieflow/电影后期 | 已有 LUT（6 个 .cube）+ apply_lut + lut_strength |
| C4 | 音画/对白/**唇形** | 可灵原生音频、Sora2 唇形、Agnes 片内音 | 已有 TTS（豆包/Edge/piper）+ Agnes 片内音路径；lip_sync 待联调，缺"对白镜音频策略"统一 |
| C5 | 节奏/转场/剪辑 | wind-comic 编辑器、movieflow 决策树 | 已有 xfade 转场 + edit_advisor + ducking/loudnorm；缺发布健康门禁 |
| C6 | 无硬伤（坏帧/黑帧/崩坏/漂移） | VideoClaw VLM 质检、wind-comic 质量回路 | 已有 asset_quality_gate（确定性）+ asset_picker（A/B）；**缺 VLM 语义质检** |

### 0.2 "少人工"的现状与目标

当前 5 个门禁阶段（`proposal/script/scene_plan/assets/publish`）写 `completed` 必须
`human_approved` 或 `approved_by in {human, produce}`（见 `montage/engine/stages.py`
`_gate_allows`）。`approved_by=produce` 已是"机器收口"通道，但**没有自动审批器**去
算它——这是本轮改造的核心杠杆。

目标：新增 **Autopilot（自动审批器）**，门禁阶段先跑确定性校验 + VLM 审查，
**全绿则机器收口**（`approved_by=produce` + 决策日志），**有 critical 才升级人工**
（`awaiting_human` 携带诊断报告）。人工从"每步都点"降为"只处理机器拿不准的异常"。

---

## 1. 平台制作步骤 → 能力差距映射（拆解后落点）

| 平台/项目 | 关键步骤/机制 | montage-core 现状 | 差距与落点 |
|-----------|---------------|-------------------|-----------|
| Pavo | 一句话 → 需求卡 → 人设/场景/道具 → 分镜 → 关键帧 → 视频 → 合成 + 镜头级返工 | idea_developer(skeleton/validate/compile) 已有级联雏形，但 **bible 需 Agent 手写** | **P0** 补"idea→bible 自动生成"；**P2** 补 rework_requests |
| 可灵 | 角色一致性 / 长视频 / 原生音频 | 定妆照 + registry；duration_policy 5/10；音频走 TTS | **P1** 四视图；**P2** 长镜头优先 + native_audio；**P4** 对白镜音频策略 |
| 即梦 | 多镜头叙事 / 首尾帧 / 电影级渲染 | shot_runner 首帧/尾帧桥接已完整 | **P3** 节拍→运镜映射，强化镜头语言 |
| Sora2 / Veo3.1 | 60s 长镜头 / 唇形 / 视频延展 / 多参考图 | capabilities 已抽象 first_frame/last_frame/keyframe_chain/modes | **P2** capabilities 加 `max_duration/native_audio/lipsync/negative_prompt` |
| VideoClaw | 场记库 / VLM 闭环质检 / 无限续写 / 可干预 | bible.py + episodes.py 有系列雏形；asset_quality_gate 是确定性 | **P1** continuity 场记库 + vlm_reviewer；**P0** 续写用场记 |
| wind-comic | 导演控制台（blocking 构图审计）/ 分段重拍（-c copy）/ 多引擎竞赛 / 情绪运镜 / sketch-lock | blocking 字段已有；selectors 单路由；shot_runner 有 retry_ids+cache | **P2** race + 分段重拍；**P3** 构图审计 + 情绪运镜 + sketch-lock |
| movieflow | 平台无关中间提示词 + 适配器 + 成本矩阵 | capabilities.py 已是适配器表；visual_prompt_builder 已双提示词 | **P2** 显式 StandardVisualPrompt 中间层 + 镜头分层成本 |
| 行业 SOP | AI 生成 + 人工四步把控（选角色/定一致/控情感/控节奏） | 门禁审批模型已对应 | **P0** 把"人工把控清单"写进门禁产物字段 |

---

## 2. 分阶段进化方案

> 每阶段独立可验证、可回滚。P0 是"少人工"的骨架，P1 是"电影级一致性"的命门，
> P2 是"生成策略"，P3 是"导演语言"，P4 是"音画后期"，P5 是"体验与交付"。

---

### P0 — Autopilot 自动审批骨架（最少人工的开关）

**目标**：一条命令从 idea 跑到成片，人工只在机器判不过时介入。

**改动清单**

1. 新增 `montage/engine/autopilot.py`：
   - `review_stage(project_dir, stage) -> ReviewResult`：按阶段调度确定性校验
     （`script_validator` / `edit_advisor` / `asset_quality_gate` 已有）+ 决策日志，
     返回 `{pass, findings[], needs_human}`。
   - `auto_approve(project_dir, stage)`：`pass` 则调 `CheckpointStore.write(stage,
     "completed", approved_by="produce", note="autopilot: <findings 摘要>")`；
     否则写 `awaiting_human`，把 findings 写进 `decision_log category=review`。
   - 严格约束：**不伪造 human_approved**，机器收口显式记为 `produce`。
2. 新增 `montage/tools/idea_developer.py` 的 `operation=autowrite`：在现有
   `compile` 基础上，允许 Agent 用一句话调用 LLM **生成 bible 草稿**（脚本层由
   导演 Agent 补，代码仍只做确定性转换），把 `--idea` 从"只收编圣经"推进到
   "idea→bible→script→scene_plan 全自动草稿"。
3. `montage/pipelines.py` 各管线加顶层字段 `autopilot`（`"off" | "gated" | "full"`），
   `proposal_packet` schema（`montage/schemas.py`）加 `autopilot` 与
   `human_checkpoints`（只在这些阶段强制等人，如 `["proposal","script"]`）。
4. `montage/engine/produce.py`：`STEP_IDS`/`GEN_STEP_IDS` 前插入 `autopilot` 步骤，
   依次对 gated 阶段跑 `auto_approve`；`--autopilot full` 时全部机器收口，
   `--autopilot gated` 时按 `human_checkpoints` 保留人审。

**验收**：`python -m montage produce <dir> --idea "一句创意" --autopilot full`
在确定性校验全绿时，无任何人工 approval 跑通 `proposal→…→publish`；任何 critical
都会停下并给出可读的 `proposed_fix`。

---

### P1 — 电影级一致性闭环（对齐可灵，命门）

**目标**：角色/场景/道具跨镜头不漂移，坏素材被 VLM 语义层拦截。

**改动清单**

1. **四视图角色卡**：
   - `montage/schemas.py` 的 `SCRIPT_SCHEMA.characters.items` 加
     `turnarounds`（`["front","side","back","three_quarter"]` 可选）。
   - `shot_runner.py` 生成定妆照时（`needed_portraits` 分支）按 `turnarounds`
     生成多视图，写入 `asset_manifest.reference_assets`（`kind=portrait` 多张，
     复用现有 `reference_assets[]` 结构，`character_id` 相同即可）。
   - `capabilities.py` 已用 `apply_image_refs` 填参考图，四视图直接多 URL 注入。
2. **场记库（continuity）**：
   - 新增 `montage/engine/continuity.py` + `artifacts/continuity.json`（不进
     pipeline produces，sidecar 同 `series_bible`）。
   - 字段：`characters[]`（当前位置/服装状态/手持道具）、`props[]`（在场/缺失）、
     `locations[]`、`last_shot_id`。`shot_runner` 每镜生成前注入"上一镜场记摘要"
     进提示词（走 `visual_prompt_builder` 的 `style_context` 之外的独立通道）。
   - `episodes.py` 续写时从上一集 `continuity.json` 读入，实现**跨集一致**。
3. **VLM 闭环质检**：
   - 新增 `montage/tools/vlm_reviewer.py`（`capability="analysis"`，provider 优先
     `dashscope` 通义千问 VL，`DASHSCOPE_API_KEY` 已复用；留 OpenAI-compatible
     通用接口）。
   - 输入 `{media_path, mode: first_frame|video_clip, shot, character_registry,
     script, expected}`；输出 `{ok, score, issues[]}`，issues 每项含
     `severity/kind(人物不一致|道具丢失|场景错位|崩坏|构图)/message/proposed_fix`。
   - `shot_runner._check_quality`（已可注入）后追加 VLM 审查：确定性 gate 过 →
     VLM 过 → 收进 manifest；VLM critical → 计入 `retryable_ids` 换 seed 重生成。
   - `pipelines.py` 的 `_ASSETS_CORE` 加 `"vlm_reviewer"`。
4. **门禁升级**：`gates.py _completeness_blockers` 扩展为"确定性 critical +
   VLM critical 均挡门"（VLM 不可用/未配密钥时降级为 warning，不阻塞，向后兼容）。

**验收**：同一角色跨 ≥5 镜，VLM 复查"人物漂移"命中率显著下降；VLM 能揪出
"定妆照是黑发、镜头里变金发"这类语义不一致并触发重生成。

---

### P2 — 生成策略升级（竞赛 / 中间格式 / 分段重拍 / 长镜头）

**目标**：单镜质量追平头部长视频模型，返工只重拍坏段。

**改动清单**

1. **多引擎竞赛（race）**：
   - `montage/providers/selectors.py` 加 `race` 模式：`VideoSelector` 新增
     `race_providers`（提案 `proposal_packet.allowed_providers` 内多供应商）。
   - 同镜并行（或串行受 RPM 限）生成 N 份 → `asset_picker`（确定性）+ VLM
     （语义）双评分，首个过阈值或最高分胜出；失败镜头标注 animatic，不拿静图冒充。
   - 提案 `proposal_packet` 加 `race_providers`、`race_budget_factor`。
2. **平台无关中间提示词**：
   - 新增 `montage/providers/prompt_adapter.py`：定义 `StandardVisualPrompt`
     （foreground/midground/background/camera/shot_size/dialogue/emotion），
     按 `capabilities.py` 表转成即梦/Agnes/万相/清影各自格式。
   - `visual_prompt_builder` 保持不动，`prompt_adapter` 在其下游消费
     `first_frame_prompt/video_prompt` 做供应商方言化（复刻 movieflow 思路）。
3. **分段重拍（rework_requests）**：
   - 新增 `montage/tools/ffmpeg_compose` 的 `operation=retake_segment`（或
     `montage/compose/effects.py`）：`-ss/-to` 定位坏段重生成，未改段 `-c copy`
     字节拷贝，时长不变 → 时间线/字幕/EDL 免重算。
   - 新增产物 `artifacts/rework_requests.json`（sidecar）：`[{shot_id, segment,
     revision_note, regenerate}]`，`shot_runner` 只重跑清单内镜头（复用
     `retry_ids` + `generation_cache`）。
4. **长镜头优先 + 能力扩展**：
   - `capabilities.py` 的 `VIDEO_BY_TOOL` 加 `max_duration` / `native_audio` /
     `lipsync` / `negative_prompt` 字段（可灵长视频、Sora2 60s、Seedance 4min、
     Agnes 片内音）。
   - `policy.load_loop_policy` + `shot_runner` 选型时优先 `max_duration ≥ 镜头时长`
     且 `native_audio` 的供应商，减少拼接与对白失同步。
5. **镜头分层成本精化**：`shot_budget_class`（hero/talk/establishing，schema 已有）
   P2 落地执法：hero 镜走高质量模型，establishing 走静图+Ken Burns 或低成本模型，
   dry_run 估算按 class 分档计费。

**验收**：同镜可配置双供应商竞赛；坏段重拍不重编码好段；长镜头不再被无谓切碎。

---

### P3 — 导演语言层（节拍→运镜 / 构图审计 / sketch-lock）

**目标**：镜头语言自动贴合情绪与节拍，生成前拦截构图硬伤。

**改动清单**

1. **节拍→运镜映射**：
   - 新增 `montage/director.py`（确定性 dict）：`beat_to_camera =
     {hook: push_in, escalation: handheld, reveal: zoom_in, landing: pull_out,
     chase: handheld, farewell: pull_out}`。
   - `edit_advisor.py` 与 `visual_prompt_builder` 消费：镜头缺
     `shot_language.camera_movement` 时按 `narrative_role` 推导补写。
   - `playbooks` 每本加 `motion.beat_camera`（沿用现有 playbook 结构，向后兼容）。
2. **构图审计（生成前）**：
   - `script_validator.py` 新增 `purpose=composition`：基于 `blocking.x/z`
     （left/center/right × near/mid/far）+ `shot_language.shot_size`，确定性检查
     "同场两角色 blocking 重叠 → 遮挡风险"、"特写却给了 far 站位 → 出画风险"，
     输出 `proposed_fix`。生成前拦截（对齐 wind-comic Director's console）。
3. **节拍覆盖校验**：
   - `script_validator.py` 新增 `purpose=beat_coverage`：`scenes[].narrative_role`
     是否覆盖 `structure.{hook,escalation,reveal,landing}` 四拍，时长占比
     （≈15%/50%/75%/20% 附近）异常即 warning。
4. **sketch-lock 骨架锁定**：
   - `shot_runner` 加 `sketch_lock` 开关（proposal 透传）：首帧前先出低分辨率
     构图草图（复用 `visual_prompt_builder` 的负向/风格段），终版生成锁该构图。
     成本极低，规避"镜头语言被扩散模型抽奖"。

**验收**：无运镜的镜头自动获得节拍驱动的运镜；`purpose=composition` 在生成前
报出遮挡/出画；四拍缺失即 warning。

---

### P4 — 音画与后期电影感（对白策略 / 健康门禁 / 电影感）

**目标**：对白不出戏，成片无技术硬伤，色调统一。

**改动清单**

1. **对白镜音频策略**：
   - `proposal_packet` 加 `dialogue_audio_mode`（`native | tts | lipsync`）。
   - `shot_runner`/`voice_director`：对白优先镜头（`audio_prompt.dialogue` 非空）
     优先走 `native_audio` 供应商（Agnes 片内音、可灵原生音频），否则 TTS +
     `lip_sync`（待联调补齐）。
2. **发布健康门禁**：
   - 新增 `montage/tools/film_health.py`（capability=analysis）：publish 前 ffprobe
     全片（分辨率/时长/fps/码率/音轨/劣化镜头聚合），产出 `film_health.json`。
   - `pipelines.py` publish 阶段 `produces` 加 `film_health`（或作为 sidecar 检查），
     critical 项挡 `export_bundle`。
3. **电影感后期**：复用 `apply_lut`（lut_strength 已有）+ `cinematic_21_9` 裁切 +
   可选 film grain（`compose/effects.py` 加 `grain` 滤镜，无新依赖）。

**验收**：对白镜不再"旁白声+无口型"；发布前自动报告全片健康；色调跨镜头一致。

---

### P5 — WebUI 导演控制台 + 交付生态

**目标**：把上面的机器能力变成"导演可看、可勾选返工"的界面。

**改动清单**

1. **WebUI**（`montage/webui/`）：
   - 分镜 blocking 俯视图（读 `blocking.x/z` 渲染 stage 图）；
   - 逐帧审查面板（接 `frame_sampler` + `vlm_reviewer`，框选坏段 → 生成
     `rework_requests`）；
   - 阶段进度 + 机器收口/人工待审状态可视化（读 `checkpoint_*.json`）。
2. **剪映草稿导出**：`export_bundle` 加剪映草稿格式（参考 ArcReel，国内创作者刚需）。
3. **模板市场**：成功项目 → 可复用模板（`format_card` + `bible` + `proposal` 打包）。

**验收**：普通创作者能在 WebUI 完成"确认提案 → 审查分镜 → 勾选返工 → 出片"。

---

## 3. 优先级矩阵（影响 / 成本 / 风险）

| 阶段 | 影响 | 成本 | 风险 | 建议顺序 |
|------|------|------|------|----------|
| P0 Autopilot | 高（直接减人工） | 中 | 低（复用 produce/checkpoint） | **第一** |
| P1 一致性闭环 | 最高（电影级命门） | 高（VLM 接入） | 中（VLM 成本/稳定性） | **第二** |
| P2 生成策略 | 高（追平头部模型） | 高 | 中（供应商联调） | 第三 |
| P3 导演语言 | 中高（镜头质感） | 低（确定性规则） | 低 | 可与 P0 并行 |
| P4 音画后期 | 中（对白/健康） | 中 | 中（lip_sync 依赖） | 第四 |
| P5 体验交付 | 中（易用性） | 中高 | 低 | 最后 |

**最小可行闭环（MVP，先做这三件）**：
1. P0 Autopilot（`--autopilot full` 一条命令跑通，人工兜底）；
2. P1 的 VLM 质检（`vlm_reviewer` 接进 shot_runner，坏素材拦截）；
3. P3 的节拍→运镜 + 构图审计（纯确定性，零新依赖，立竿见影）。

---

## 4. 风险与降级（保持向后兼容）

- **VLM 不可用/未配密钥**：`vlm_reviewer` 状态 `NEEDS_CONFIG`，质检降级为
  `asset_quality_gate` 确定性通道，不阻塞（同 `MONTAGE_RELAX_GATES=1` 语义）。
- **机器收口误判**：Autopilot 只对"确定性全绿 + VLM 无 critical"收口；任何
  critical 一律 `awaiting_human`，且全程写 `decision_log category=review` 可回溯。
- **供应商能力差异**：全部走 `capabilities.py` 能力表降级，不按供应商名猜参数。
- **成本失控**：Autopilot 不改变 `budget_ceiling_usd` 硬停 + dry_run 估算语义；
  race 模式用 `race_budget_factor` 封顶。
- **许可证**：新增代码全部自创；参考 ArcReel（AGPL）只借功能清单，不复制其实现。

---

## 5. 一句话总结

- **P0 给"少人工"装上开关**：Autopilot 机器收口 + 异常升级人工。
- **P1 给"电影级"装上命门**：四视图 + 场记库 + VLM 语义质检。
- **P2 给"追平头部"装上引擎**：竞赛 + 中间提示词 + 分段重拍 + 长镜头优先。
- **P3 给"镜头质感"装上导演**：节拍→运镜 + 构图审计 + sketch-lock。
- **P4/P5 给"成品"装上专业后期与交付**。

现有资产（预算账本、确定性门禁、镜头分层成本、多供应商显式路由、可审计）是
开源同类里最扎实的一档，本方案在其上做增量，不推翻、不破坏 MIT 边界。
