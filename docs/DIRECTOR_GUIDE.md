# DIRECTOR_GUIDE — 剧本与分镜执导手册（原创）

> 给"导演"（LLM Agent / 人类操作员）的**创作层**操作规程，与 `AGENT_GUIDE.md`
> （工具与状态机）互补。本手册回答"怎么写出值得拍的剧本与分镜"，不回答"用什么工具"。
> 借鉴业界通用编剧方法论与剧本结构共识，文本为 montage-core 原创。

## 1. 先建节拍地图，再写对白

写作顺序：**节拍地图 → 人物卡 → 分场 → 对白**。不要倒着来。

四拍结构（短片够用；长片可加一个中点转折）：

| 节拍 | 位置 | 任务 |
|------|------|------|
| hook（钩子） | 开场 15% | 建立人物与冲突种子，抛出"为什么看下去" |
| escalation（升级） | 中段 50% | 矛盾逐级抬升，信息随冲突释放（不靠旁白直给） |
| reveal（揭示） | 75% 处 | 关键真相/立场反转，让前文伏笔在此兑现 |
| landing（落地） | 收尾 20% | 结局回答开场渴望，给观众一个最终情绪或行动 |

写入 `script.structure` 字段；每场戏对应哪个节拍写在 `scene_plan.scenes[].narrative_role`。

## 2. 人物卡（叙事片必填）

叙事片（有角色、有对白）在 `script.characters[]` 建立**人物卡**，这是外观与性格的
单一事实源：

| 字段 | 语义 | 示例 |
|------|------|------|
| `id` | 稳定标识（其他阶段只引用它） | `li_ming` |
| `name` | 展示名 | 黎明 |
| `role` | protagonist / antagonist / supporting / functional / narrator | protagonist |
| `appearance` | **外貌锚点**：逐字复制到分镜 `character_registry.appearance`，禁止改写 | 黑发青年，左眉一道旧疤，深灰风衣 |
| `outfit` | 服装锚点：逐字复制到 `outfit_anchor` | 深灰长风衣，白色高领 |
| `personality` | 性格 1-2 句 | 外冷内热，行动先于言语 |
| `speech_style` | 用词/口癖/语速——对白必须与其一致 | 短句，少用形容词，语速偏快 |
| `voice_id` | 音色：voices 表 id（`female_soft`）或供应商原生名；空则按角色/性别解析 | `male_low` |
| `relationships[]` | 与其他角色关系 | ["yan_zi: 旧识，互不信任"] |
| `arc` | 本片人物变化一句话 | 从逃避责任到主动承担 |

**铁律**：
- 外观锚点一旦写入人物卡，分镜与提示词阶段**逐字复制**，不得 paraphrase、不得
  重新发明——这是跨镜头角色一致性的根基（`visual_prompt_builder` 的
  `appearance_anchor` / `character_registry` 自动消费）。
- 每句对白必须能指到某个 `characters[].id`（禁止"有人说"）。写入
  `sections[].lines[]`（`speaker_id` + `text`）；`narration` 只作旁白或由 lines
  拼出的朗读稿。`script_validator`（`purpose=completeness`）缺 speaker 时报告 warning。
- 只有动作没有对白的角色也要有人物卡。
- 主环境写入 `script.environment`（对象或一句描述），不要只藏在旁白散文里。

## 2b. 地点卡（交叉方位，给 Agnes 2.5 用）

写在 `series_bible.locations[]`。`sensory` 是这场戏的**布景图**，同 `location_id` 各镜共用；compile 会抄进 `scene_plan`，Python **只裁切、不发明**地标。只写地名、不写方位时，2.5 提示词会扁。

三轴可交叉，按看见的写，不必填满格子；没出现的方位不要写空括号：

| 轴 | 用词 |
|----|------|
| 水平 | 左侧、中间、右侧 |
| 纵深 | 近处、中景、远处、更远处 |
| 垂直 | 上方、中段、下方 |

同场锁定：下一镜不得把「右侧远处宫殿」改到左侧。大远景用全图；特写只留近处，远处用一句轮廓带过。

示例（3～6 句即可）：

```text
古风石板广场。左侧近处有仙气薄云贴地缓缓流动；画面中下方是湿润石板倒映天光；右侧远处是宏伟宫殿，屋脊入雾；正中更远处天际还有一层淡金霞光。近处衣料轻摩，右侧远处檐铃。
```

关键镜再写人物 `blocking`（`x`/`z`，可选 `y`）和走位一句（从哪一格到哪一格）。

## 3. 对白预算：在剧本阶段算死（防生成期爆时长）

视频生成有**时长网格**（即梦 5s/10s；Agnes 2.5 单镜 4–12s）。对白必须在 script 阶段就装进
网格，拖到生成阶段才发现超长 = 返工 + 浪费成本。

规则：
- **口播密度按 5 字/秒**（默认，4-6 可调）：5s 镜段 ≈ 20-30 字；10s 镜段 ≈ 40-50 字。
- 即梦闭环：`scene.duration_seconds` 必须是 5 或 10 的倍数；一段对白放不进网格就
  **拆场/拆镜**，禁止截断对白。Agnes 2.5 更长的镜由 runner 切段，对白仍按字数估，装不进就拆镜。
- 验证：运行 `script_validator` 工具（`purpose=dialogue_budget`），它会按网格核算
  每段对白字数与时长，超载返回具体镜头与建议拆法。

## 4. 可拍性铁律（AI 视频）

**心理不可拍，动作可拍。** 写进 `scene_plan.scenes[].description` 与镜头
`visual_details.subjects[].action` 的必须是**可见的物理动作**：

- ❌ "她感到害怕"（心理状态，模型无法生成）
- ✅ "她后退两步，手扶着墙，指节发白"（可见动作 + 可见细节）
- ❌ "他内心挣扎" ✅ "他握紧又松开拳头，看向门口三次"

`script_validator` 的 `purpose=filmability` 会用启发式扫描心理动词
（觉得/感到/认为/想起/害怕/挣扎…），命中即报 warning，导演须改写为可见动作。

## 5. 分镜阶段：一致性守则

- 先跑 `script_to_scene_plan` 拿骨架（返回 JSON，由导演写入 artifact）。已有精修的
  `scene_plan` 默认不覆盖。镜头嵌在 `scenes[].shots[]`，不要在本阶段写 `shot_prompts.json`。
- `scene_plan.character_registry[]` 的 `id` 只引用 `script.characters[].id`，
  `appearance`/`outfit_anchor` **逐字复制**自人物卡（同上 §2）。
- `scene_plan.scenes[].character_ids[]` 标注本场出镜角色，供 assets 阶段检索。
- 每个场景的 `description` 用**镜头可读语言**（空间+人物+动作+光线），不要写情绪
  形容词（情绪交给 `shot_language` 与对白）。

## 6. 风格一致性：playbook 锁定

proposal 阶段从 `montage/playbooks/` 选择一个 playbook（`get_playbook(name)`），
把 playbook 写入 `proposal_packet.playbook`。`visual_prompt_builder` 在未传入
`style_context` 时会从该字段自动注入；仍可显式传 `style_context` 覆盖。
锁定写入决策日志（`category=style, subject=playbook`），中途不换。

compose 阶段用 `compose_planner` 把每镜转场/LUT/字幕槽写成 `compose_plan`，再编译为
`edit_decisions`（assemble 只读后者）。`render_kind` 默认 `ai_clip`；片头/漫画框/
逐字花字本轮用 ASS / Ken Burns / `showcase_card` 近似，不要另起 Node 渲染器。

## 7. 剧本范式参考

写对白与场景前，用 `prompt_library_retriever` 检索：
- `category=screenplays` — 公有领域经典剧作的情境范式（对峙/誓言/独白/试探…），
  学**结构**与**语感**，禁止照抄台词；
- `category=scripts` — 结构模板（三幕/英雄之旅/人物弧光/场景三要素/对白五用）。

## 8. 一致性强制流程（定妆照 → 参考图 → 图生视频）

角色跨镜头一致是 AI 视频质量命门，**不要靠提示词碰运气**。assets 阶段用
`shot_runner` 编排（先 `dry_run=true` 看估算，确认后再 `dry_run=false`）。
缺一环就在 assets 阶段补，不要跳到 compose：

1. **定妆照 + 四视图（导演档第 4 步，compile 前）**：`visual_prompt_builder`（`purpose=portrait` / `turnaround`）→
   `image_selector`，落盘 `assets/images/portrait_<char_id>.png` 与
   `turnaround_<char_id>.png`，写入 `asset_manifest.reference_assets`
   （`kind=portrait` / `turnaround`）。口播 `spoken_explain` 跳过。四视图默认定妆；
   `characters[].skip_turnaround=true` 可跳过该角色。
2. **场景参考图 / 道具白底**：地点走 `purpose=scene_ref`（`kind=scene_ref`，对齐
   `locations[].id`）；道具白底单主体无手持（`kind=prop`）。
3. **每镜首帧图带参考图**：`purpose=shot` 产首帧提示词 → 按能力表填参考图
   （Agnes `reference_urls` + `image_reference` / 即梦 `image_urls`；万相无参考则纯文生）。
4. **图生视频**：即梦/Seedance 仍可走首尾帧（同空间 `cut=bridge` 时上一镜尾帧当下镜首帧；`cut=hard` 或双方 `location_id` 都有且不等才断开）。**Agnes 2.5 Flash** 不要尾帧静图、不要 `videos[]`：同场衔接用同一套定妆/四视图 `images[]`（最多 5 张），超 12s 切段后 ffmpeg 拼接。禁止 `first_frame` 与 `images[]` 混用。无 `first_frame` 能力不要编造参数。
5. **一致性锚点注入**：playbook 的 `consistency_anchors`（≤2 条）经
  `visual_prompt_builder` 自动进入首帧图【风格】段。

单镜失败看 `retryable_ids` 重跑，不要整片重来。发现角色漂移 → 重出定妆照，
不要在第 3 步硬改提示词。

## 9. 节拍 → 运镜（P4，确定性补空）

缺 `shot_language.camera_movement` 时，compile / `script_to_scene_plan` 按幕的
`narrative_role` 查表（`hook→dolly_in`，`escalation→handheld`，`reveal→zoom_in`，
`landing→dolly_out`；口播 playbook 全 `static`）。**已有值（含显式 static）不改**。
别名：`establish_context≈hook`、`build_tension≈escalation`、`deliver_payload≈reveal`、
`resolution≈landing`。构图审计（`purpose=composition`）只拦特写+远站位；四拍覆盖
（`purpose=beat_coverage`）缺拍只 warning/suggestion，单幕短片不必硬凑四拍。
