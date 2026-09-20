# ROLES — 多角色协作审核协议（编剧/导演/美术指导/动作指导/特效指导/剪辑导演）

> 角色 = 协议文档 + 确定性工具 + 停点，由 Agent 扮演（代码零 LLM，创意决策外置）。
> 完整方案论证见仓库计划文档；本页是落地执行版，与
> [REVIEWER.md](REVIEWER.md)、[DIRECTOR_GUIDE.md](DIRECTOR_GUIDE.md)、
> [ART_DIRECTOR.md](ART_DIRECTOR.md)、[ACTION_DIRECTOR.md](ACTION_DIRECTOR.md)、
> [VFX_DIRECTOR.md](VFX_DIRECTOR.md)、Skill `montage-produce` 同锁。

## 1. 角色-停点映射表

| 停点（status） | 角色审范围 | 汇报纪律 |
|----------------|-----------|----------|
| `await_setup` | 编剧自审 + 导演审：时长定夺/标题/画幅/playbook | 汇报并读 REVIEW.md + review_log 摘要（V24） |
| `await_outline` | 编剧自审 + 导演审：角色/地点 sensory/主题/四拍/金句≤6；**美术帽轻审**：风格基调 vs format_card 用户需求（P0-8，同轮换帽） | 同上 |
| `await_design` | 编剧自审 + 导演审：appearance/outfit/空镜/道具；**子 Agent 三维度盲审在人审前**（仅一次） | 同上；SA findings 已写 review_log |
| `await_cast` | 美术指导：定妆/四视图/空镜/道具图（风格/美感） | 同上 |
| `await_shots` | 导演执法①（背景锚定/连续性）+ **点位一**：美术+动作+**特效**并行审源字段（阻塞式，首帧前不烧钱）；特效帽制定每镜 `vfx[]`（P0-8） | 同上 |
| `await_frames` | 美术读首帧（抽检规则 V20）；重抽前六步归因 | 同上 |
| `await_final_prompt` | **点位二**：美术+动作+**特效**子 Agent 首审（仅一次，不占额度 V36/V47；P0-8 并行 3）→ 循环复审主 Agent 换帽 | 同上 |
| `await_clips` | 动作：VLM+关键帧抽检；导演：节奏；**美术**：风格终检（vlm video_clip 对照 playbook + `vfx[]` 美学，P0-8）；**特效**：VLM 抽帧看特效观感（P0-8，与美术同轮换帽）；**剪辑导演**：`edit_plan` 客观量规人审（m1/m2/m5/m6+身份漂移；首审 first_pass 不占额度，返修 ≤4 轮）——**assemble 前唯一放行口就是本停点的人点头** | 同上 |
| `await_episode`（系列根） | 默认纯用户裁量；可选导演帽集间审（拍板后启用） | — |

可灵环（`video_loop=kling`）顺序例外：await_design → compile/await_shots → await_cast →
await_frames → await_final_prompt。**webui 看板只读 review_card+progress，看不到
review_log——角色审结论以 CLI 停点呈报为准（V31）。** P0-7c 之后看板多了 SSE 实时
刷新，但刷新的仍是同一批只读视图，**不是**第二道放行口（结论还是 CLI 停点）。

## 2. 轮次规则

- 每角色 REVISE **≤4 轮**；导演审核处 **≤6 轮**；不设单停点合计上限。
- **点位一+点位二共用每角色 4 轮额度**（V11/D16，防终审循环绕过上限）。
- **特效帽纳入既有额度（P0-8）**：`vfx_director` 无新增额度——compile 确定性
  自审不占额度（findings 通道零 LLM 成本），子 Agent 首审 first_pass 免费，
  换帽复审走本角色 ≤4 轮。
- **额度规则（V36 定案）**：子 Agent 关卡首审（剧本 SA 终审/点位二首审）**不占额度**；
  返修轮从换帽复审起算。**记账（V47）**：首审 record 带 `phase=first_pass`，
  返修轮一律 `phase=revise`——review_logger 的 summary/振荡检测只数 revise 行。
- **record 纪律（V21，承重墙）**：每轮 REVISE 必须 `review_logger record`
  （decision/findings/round/scores）；不 record 的轮次视为无效轮（不计上限、问题视为未处理）。
- **轮次耗尽出口（V28）**：任一额度耗尽 → 导演裁 **PASS WITH WARNINGS**：
  未解决 findings 落盘 review_log + 升级用户停点（接受→放行/打回→回循环）。不是静默放行。

### subject 枚举表（V30，record/summary 的匹配键地基）

| 枚举值 | 对应审读位置 |
|--------|--------------|
| `setup` / `outline` / `design` | 三停点角色轮 |
| `cast` | 定妆类审+重抽 |
| `checkpoint_1` | 点位一源字段预审 |
| `final_prompt` | 点位二终审 |
| `frames` | 首帧抽检 |
| `clips` | 单镜视频审 |
| `retry` | 重抽前归因 |
| `draft_selection` | 多候选选优 |
| `edit_plan` | 剪辑导演客观量规审（assemble 前） |

表外值 record 记 warning 不拒绝；summary 单列"协议违规"节。手填 round 仅参考，
summary 按 timestamp 序自动编号（V30）。

### 日志分工（V35）

`decisions.jsonl`=剪辑决策 / `cost.jsonl`=成本审计 / `artifacts/review_log.jsonl`=角色评审。
三者互不替代；review_log append-only，不覆盖不重写。

## 3. 字段域表与编辑路径

| 域 | 字段/维度 | 归属 |
|----|----------|------|
| 美术域 | appearance/outfit/location_sensory/objects/光线色彩/playbook 一致性/细粒度执法①/身份动作分离⑦ | 美术指导 |
| 动作域 | subjects.action 全字段（verb/manner/body_part/contact 整组写）/动作-对白互斥/招式时长/单镜单动作① | 动作指导 |
| 导演域 | 节拍/景别策略/时长/切法/叙事/背景锚定与连续性①/仲裁/返修决策⑥ | 导演 |
| 剪辑域 | 切点帧位/转场/负空隙/镜长与拍网格/音频能量对应（P0-4 量规） | 剪辑导演 |
| 特效域（P0-8） | `shots[].vfx[]` 制定（layer/kind/onset/duration/intensity）/密度红线/post kind 白名单/`audio_prompt.sfx` 同步 | 特效指导制定；**美术审风格** |
| 在场清单域（B2.5，2026-09-19） | `bible.scenes[].shots[].presence`：`location{id,zone,time_of_day,light}` / `characters[{id,zone,state,costume_state,enters,exits}]` / `props[{id,holder,zone}]` / 空镜 `empty_reason` | 内容归属：场景方位·时段·光位=美术；人物状态·服装=美术+动作；道具归属与位置=美术；`enters/exits`=导演。**执笔=编剧** |
| 承接表（B2.5） | 编译器产出的 `shots[].continuity{must_keep,changed,missing}`（**只读，禁手改**）；`missing` 每条要么编剧补写本镜、要么导演裁定上镜 `exits:true` | 导演（连续性①）；执笔=编剧 |
| 编剧域 | 上述全部的"笔"——按 finding 执行修改 | 编剧 |

**字段编辑路径（D15/V13/V23/V29 唯一事实源）**：

- **镜头级视觉修复**（subjects/objects/blocking/shot_language/**vfx**）：写
  `bible.scenes[].shots[]`（schema 原生支持，V27 已补 shot_language 声明；
  P0-8 已补 vfx/hero_moment 合并）→
  **先手动重编译再 `--resume`**：
  `python -m montage run <dir> idea_developer --input inputs.json`
  （inputs 仅 `{"operation":"compile"}`，bible 由工具从项目目录直读，V29）→ `--resume`。
  前置检查（V37）：先读 produce_progress.json 确认 status 为 `await_*`（防竞态）。
  **vfx 禁止直改 scene_plan**——重编译从零再生 shots，直改必被冲掉（P0-8）。
- **结构性/跨镜字段**（角色 appearance/location.sensory）：改 bible 前跑 dry_run
  （shot_dry_run）比对受影响镜+估费 → 导演权衡 → 改后同样先重编译。
- **scene_plan 直改** = 仅限 bible 不支持字段的兜底，须记 review_log。
- **shot_id 纪律（V27）**：拆镜/加镜/删镜必须整场对齐且每镜显式 shot_id——
  overlay 的索引兜底取 bible_shots[0] 会把错误视觉覆盖到未匹配镜；
  compile 现在会对镜数不齐出 warning（守护卫已生效）。
- **action 整组写（V27）**：verb/manner/body_part/contact 四字段整组写，
  只写 verb 会残留 plan 侧"说话/站立"stub 的 manner。
- **禁改白名单（V18）**：不碰 retry/通配/clip_path 等生产链字段。

## 4. 仲裁三出路与振荡检测

- **仲裁三出路（V3）**：裁改→编剧执行 / 驳回 finding→记 warning 继续（驳回必须写理由
  记 review_log+呈报用户，用户可推翻 V9）/ 裁不动→升级用户停点。
  **仲裁结论有约束力**：写入后该角色不得重提同一 finding。
- **振荡检测（V22 简化版）**：同 (role, subject, field) 连续 2 个 revise 轮即 osc
  （宁滥勿缺：假阳性由仲裁驳回兜底，假阴性空转烧轮次）；regression 以 message 相似度辅助；
  **域冲突**（不同角色同字段）不算振荡→走仲裁；first_pass 行不参与判定（V47）。
- **跨点位翻转盲区（V25，已知取舍）**：匹配键 subject 区分 `checkpoint_1`/`final_prompt`，
  同一问题在两点位间来回翻转时同键判定**不会触发**；P3→P1、RT→P1 回边也不经振荡检测
  （检测只接 REVISE 出边）。此盲区由共用 4 轮额度兜底（耗尽→PASS WITH WARNINGS+升级用户，
  V28），**勿误以为振荡检测全覆盖**。
- **用户至上条款**：用户明确要求 > 工具兼容规则——不判 critical，只记 finding 提示风险。

### 4a. 导演四维评分规范

评分仅导演两处（剧本循环审核处 ≤6 轮、点位二终审）；分数是**趋势信号不作放行条件**：

- **四维度各 1-5 分**：节拍结构 / 人物弧光 / 对白质量 / 可拍性。
- **锚定描述禁裸数字**：每个分数必须能指到具体依据；**findings-first**——
  某维度扣分须引用至少一条 finding（无 finding 的维度默认满分）。
- **时长贴合不入评分**：时长是客观校验（duration_advisor），不是导演主观分。
- **无 target 时第一轮先定 target**，连同质量 findings 一轮返回，不空转一轮。
- 分数写入 review_log（record 的 `scores` 字段），可追踪跨轮趋势。

### 4b. 剪辑导演客观量规（P0-4，assemble 前人审）

**挂点说明**：量规审挂在 `await_clips` 停点内（`DIRECTOR_AWAIT` 没有独立
`await_edit_plan` 状态，别去找它——`montage/engine/director.py` 的回顾卡在
`await_clips` 会多带一行 `metrics_summary`）。原因是量规的输入与 clips 审是同一批
产物（`scene_plan`/`shot_prompts`/`vlm_review`/`soundtrack`/`identity_memory`），
再插一道独立放行口等于同一批证据连点两次头，信息量为零。

剪辑导演（`edit_director`）在此停点用**可算的数**审剪辑决策，不再靠主观感受。
一次 `review_logger operation=metrics` 就是一轮：

```bash
python -m montage run <dir> review_logger --input inputs.json   # {"operation":"metrics"}
```

- 工具读产物（`scene_plan`/`shot_prompts`/`vlm_review`/`soundtrack`/`identity_memory`）
  算齐指标 → 写 `artifacts/edit_metrics.json` → 缺省直接落一行 review_log
  （role=`edit_director`、subject=`edit_plan`；`record=false` 只算不落）。
- **每条 finding 都带 `metric` / `value` / `threshold`**，4 轮 REVISE 有据可依；
  `scores` 存各指标实测值，summary 的 `metrics_summary` 一行可读（`!`=未过、`~`=跳过）。
- 指标（`montage/engine/edit_metrics.py`）：

| 指标 | 含义 | 阈值 |
|------|------|------|
| `m1` | prompt 相关性：本镜提示词是否带齐身份/场景/道具锚（+ VLM ok） | 1.0 |
| `m2` | 语义连贯：相邻镜须有叙事锚（同场/同章/共享角色/共享道具） | 0.9 |
| `m5` | beat-cut 同步：切点落在 BGM 拍网格（容差 2 帧）内的比例 | 0.7 |
| `m6` | 能量-视觉对应：切点密度与音频能量 Pearson r | 0.5 |
| `drift` | 身份漂移：`identity_memory` 里漂移/已触发重拍的角色数 | 0 |

- **`m3`（运动连续/光流）、`m4`（构图一致/显著性）明确不做**：那是混剪 match-cut 专属，
  本管线的镜分别独立生成，不存在流场/主体对齐约束；硬算等于为不存在的目标烧光流算力。
- 跳过的指标不算不达标（`pass=true`），只在报告里标 `reason`（如 m5 无 bpm、m6 镜数不足）；
  **不要**把 `skipped` 当"已达标"写进汇报——它是"没测到"。
- **自我满足指标（P0-5 起，`circular`）**：`auto_edit --has-bgm` 用能量波/拍网格**生成**切点后，
  `m5`（吸拍）与 `m6`（密度∝能量）必然是满分——那是 DP 的约束，不是剪辑质量的证据。
  量规层会打 `circular=true`、一行摘要标 `c`（`m5c1.0 m6c0.86`），并在
  `report.circular_note` 里具名。**circular 指标不能作为 PASS 的支撑证据**：
  放行要看 `m1/m2/drift` + 人眼；DP 不可行（bpm 都估不出）时才不标 circular。
- 量规是**审读依据不是放行条件**：能量波切点（P0-5）已接管时，m5/m6 的阈值只是回归哨兵。

## 5. 子 Agent 调用模板

1. **剧本三维度盲审（design 角色轮后、await_design 人审前，仅一次）**：
   结构（整本弧线）→内容（跨幕一致）→风格（全局统一）；维度顺序固定；
   每维度独立 findings、最后总 verdict；盲化过往轮次历史。
   critical→导演帽复核（采纳/驳回+理由记 log）；仅 minor→记 log 随停点呈报不阻塞（V26）。
2. **点位二提示词首审（await_final_prompt，美术+动作并行，仅一次）**：
   读真实提示词总览出 findings→回字段改（D15 路径+重编译）→主 Agent 换帽复审。
   **首审 record 带 phase=first_pass（不占额度，V36 定案/V47 记账）**。
3. 子 Agent 每片 ≤4 次（剧本 1+点位二并行 3——美术/动作/特效，P0-8 起并行 3；
   特效 findings 少时一轮过，并行 3 是上限不是常态）；循环复审一律换帽。

## 6. 六步失败归因（重抽/--retry 前必走）

1. 定位失败证据 → 2. 明确预期 → 3. 查上下文充分性 → 4. 归因
   （**确定性判据**：同字段连续 2 次 critical 且提示词未改=模型上限；
   提示词内部冲突/含糊=缺陷；供应商已知短板=上限）→ 5. 查内部冲突 → 6. 复核对症。

**模型上限四出口（V28）**：简化动作（回点位一改字段）/ 拆两镜（结构改动+重编译，
守 shot_id 纪律与 image_bindings 对账）/ 换供应商（美术帽瞄一眼方言重建 V14）/
带警示接受升级用户。**唯独不是继续改提示词重抽烧钱。**
headless 下保留确定性判据部分（查表零成本，V34）。

## 7. 收编保真度与多候选

- **三档保真度（V6）**：想法（核心设定为约束）/梗概（+情节走向）/完整剧本（+对白逐字）。
  判据按输入形态与信息密度，写进收编流程。
- **多候选（V5）**：仅想法输入+用户主动要求+初始稿一次。每版先 validator+estimate；
  导演 pairwise 比较选优（选优记 review_log，subject=draft_selection）；
  落选版存 artifacts/draft_alternatives.md。

## 8. 复盘（AesopAgent 轻量版）

- `produce ok` 或项目终止/长期搁置均可触发（V16）；W6 按集复盘。
- postmortem.md 数据源清单（V32）：vlm_review.json（高频 finding）、film_health.json
  （时长偏差 + P0-7 长片指标：段间响度一致性 / 连续性抽检——**只 warning 不挡闸**，
  critical 语义不扩容）、cost.jsonl（成本+重抽次数）、review_log.jsonl（轮次统计）、
  identity_memory.json（角色身份漂移/重拍定妆记录）、edit_metrics.json（P0-4 量规
  实测值与未过项）、produce_progress.steps（步骤成败）。
- 收尾时 STATUS.md 手账终写一行（V40）；仓库级反哺须用户确认。

## 9. headless 降级（V17/V34）

角色循环压缩为单轮；不派子 Agent（first_pass 不出现）；不开多候选；
振荡检测自然失效；重编译强制条款同样适用；六步归因保留确定性判据。

## 10. 返工重审

- 轻改（名字/措辞/单字段）不重跑角色循环，由下一停点确定性校验兜底（V10）。
- 触及结构（幕/角色/时长/playbook）→ 回对应停点角色轮重跑。
- await_design 人审结构改动 → 回角色轮但导演帽复查 SA 关注区（不重派子 Agent）。

## 11. 项目随行手册与手账（V38-V41）

- 新项目 init/集物化自带 `PROGRESS_TRACKER.md`（静态手册，禁改写重生成）与
  `STATUS.md`（唯一手写可覆盖文件）。
- Agent 启动三读：手册 → produce_progress.json → STATUS.md；
  status/next.argv 永远是"下一步"唯一机器事实。
- 收尾手账 ≤5 行/停点：用户拍板/改过哪些镜/坑。模板改版走 git 不回灌老项目（V41）。
- **导出包边界（V44）**：`export_bundle` 不收 PROGRESS_TRACKER/STATUS
  （与 REVIEW.md 同待遇）；交付第三方前须手动补或改 glob。

## 12. roadmap·评估后不引入清单

复审过的候选机制，均判定不引入（防后人重提重复论证）：

| 候选 | 否决理由 |
|------|---------|
| RAPO 检索增强 | `prompt_library`（中文词库+剧本范式+结构模板）已等价覆盖 |
| Mora 式独立增强 Agent | 确定性 `visual_prompt_builder` 组装更可控，无需第二个生成器 |
| FilmAgent 镜头 Debate-Judge | 运镜按 narrative_role 查表（确定性），无争议空间 |
| FRAMEWORKERS 微调路由 | 与"代码零 LLM、协议驱动"哲学冲突 |
| 开放式自由辩论 | 研究证伪收敛差；维持导演仲裁制（三出路+约束力） |
| headless 下新增确定性提示词检查工具 | 范围蔓延；现有 `await_prompt` 超长停+VLM 事后质检已兜底 |
| Agent 自造进度树/第三进度通道 | 违反单一事实源（progress.json 唯一），Agent 手写数据必然漂移 |
| 逐笔过程日志 | progress.steps/cost.jsonl/review_log 已承接，重复建设+执行负荷 |
| 随行手册 CLI 工具化 | 约 12 行复制足够（V39），CLI 是范围蔓延 |
| webui 注入 review_log 摘要 | 属范围蔓延（V31）；角色审结论以 CLI 停点呈报为准 |
