# montage-core Agent 使用指南

> 给"导演"（LLM Agent / 人类操作员）的简明操作规程。本系统是**指令驱动**的：
> 编排与创意决策由执行者按本指南 + `docs/skills/INDEX.md` + `montage/pipelines.py`
> 完成，代码只提供工具与状态管理。
>
> **创作层**另见 `docs/DIRECTOR_GUIDE.md`（人物卡/对白预算/可拍性/playbook 锁定）与
> `docs/REVIEWER.md`（阶段产物自审协议）。阶段技能页从
> `docs/skills/INDEX.md` 进入（cinematic 七阶段 + documentary / clip_factory 差异页）。

## 阶段状态机

固定顺序：`research → proposal → script → scene_plan → assets → compose → publish`

每个阶段一个 checkpoint（`checkpoint_<stage>.json`），状态机：

- `in_progress`：进入阶段时写入（无需审批）
- `awaiting_human`：产出规范产物后、需要人审批时写入
- `completed`：门禁阶段需要 `human_approved` 或 `approved_by=human|produce`。`approved_by=produce` 是机器收口，**不是人审**。旧 checkpoint 只有 `human_approved=true` 仍有效。
- 被覆盖的 checkpoint 自动归档到 `history/`，重跑不丢历史

**产物门禁（独立 gates 层）**：写 `completed` 时，`montage/engine/gates.py` 校验
该阶段 `produces` 声明的产物**存在且过 schema**（缺产物/schema 失败 → 拒绝完成，
报错提示补产物或设 `MONTAGE_RELAX_GATES=1` 降级为告警）。只拦 `completed`，
`in_progress`/`awaiting_human` 不触发；不回溯历史；对全部阶段一致生效
（非门禁阶段如 research/compose 也校验 produces）。

门禁阶段（`montage/pipelines.py`）：proposal / script / scene_plan / assets / publish。

## AutoEditor 快速路径（不进 7 阶段管线）

给本地视频 + 风格包，直接出剪辑成片。**不写 checkpoint、不消费 `artifacts/`、
不经过 `research→…→compose→publish`。** `auto_edit/final.mp4` **不属于任何 stage
的 produces**；compose 完成门禁只校验 `edit_decisions` + `render_report`，
不会因为 `auto_edit/` 存在而误判管线已完成。

```bash
python -m montage auto_edit <project_dir> --video raw.mp4 --style documentary
python -m montage auto_edit <project_dir> --clips a.mp4 b.mp4 --style cinematic --preview
python -m montage auto_edit <project_dir> --audio-only voice.wav --style fresh --is-speech
python -m montage auto_edit <project_dir> --video mv.mp4 --style beat --has-bgm --bpm 128
```

- **三选一**：`--video` / `--clips`（多段顺序拼接，段内独立分析，不先 concat） /
  `--audio-only`（黑场 + 音轨）。
- **叙事对齐（P0-2）**：先 `python -m montage run <dir> scene_pipeline --input in.json`
  产出 `artifacts/scene_index.json`；`plan` 会自动读它，把情节单元边界并进断镜点。
  `--no-scene-index` 可关掉，`--scene-index PATH` 可指定别的索引。注意单元边界仍受
  风格包 `min_hold` 约束：被吃掉的数量在 `plan` 输出的 `scene_index.dropped` 里
  显式回报（例如 documentary 的 min_hold=4.0 会吃掉 3 秒一换的段落）。
- **风格包**（`montage/style_packs.py`）：cinematic / documentary / beat / classic /
  fresh / cyber。管节奏、LUT、转场、`output_profile`；`bind_playbook` 只写入
  `plan.json` 元数据，MVP **不**调用 `visual_prompt_builder`。
- **产物**：`<project_dir>/auto_edit/{plan.json,report.json,final.mp4}`；
  中间文件与 480p 预览在 `tmp_autoedit/`（`export_bundle` 排除这两目录）。
- **缺 `project.json`**：在 `project_dir` **原地**写最小元数据，**不**调用
  `init_project`（不会落到 `<root>/projects/<id>`）。看板只扫
  `webui --root` 下的 `projects/<id>`，任意目录快速剪辑能出片但默认不出现在看板。
- **人机闭环（P0-3 版本链）**：改 `plan.json` 后用工具 `operation=replan` 或 CLI
  `--overrides '{"seg_0":{"action":"drop"}}'`。每次 `plan`/`replan`/`revert` 都会：
  1. 往 `tmp_autoedit/plan_history/` 追加一版快照（`v0001.json`…）+ 索引
     `plan_history.json`；
  2. 记一条**字段级 diff**（`重定时/改速率/增删镜/段 action/参数/转场/时长`），
     只改一个镜的速率不会被报成「整片全变」；
  3. 往 `decisions.jsonl`（`category=auto_edit`）写一行 `rev N: <reason>` + diff 摘要——
     **快照目录被 `export_bundle` 排除，所以审计痕迹落在 decisions.jsonl 这份导出的日志里**。
  - 查询/回滚：`operation=history`（带 `rev` 则连正文一起返回）、
    `operation=revert`（`{"rev": N}`）。幂等：与上一版内容等价则**不新增版本**
    （`changed=false`），所以反复跑同一 `--overrides` 不会灌水。
  - **回滚后必须重渲**（`reverted_to` 的回报里 `note` 会写明）：`revert` 只换
    `plan.json`，产物仍绑旧版。
  - `--reason "..."` 给本版命名（写进版本链与审计）；`max_versions` 是**项目级**上限
    （首次默认 50，0=不限），设过一次后续沿用，不会不传就悄悄回默认。
- **断点认 plan 版本**：`resume` 的单镜中间产物按**镜内容寻址**
  （`shot_0000_<hash>.mp4`，hash 含源/in/out/速率），`stitch/grade/profile` 的
  断点记录带 `plan_sha`。**改了 plan 再渲会真的重跑受影响的步骤**；plan 没变则照旧跳过。
  `report.render_log` 逐步记 `done/skipped` 与原因，`report.plan_sha` 是本次渲染绑的版本。
- **镜级后期特效（P0-8 后续小刀）**：`--vfx` 接 JSON 对象或文件路径
  （`{"seg_0_shot_0":[{"layer":"post","kind":"impact_flash"}]}`），只支持已落盘镜；
  空数组清除该镜特效。特效产物按 vfx 摘要独立缓存，改特效不重 trim，但拼接链会重跑。
  post 层 onset 缺省时读 `beat_map.json`，按镜窗内能量最高 bar 的起点换算成**镜内相对秒**；
  显式 onset 优先，无 beat_map 回落 0。`MONTAGE_NO_VFX=1` 跳过整条 auto_edit 特效路径。
- **能量波切点（P0-5，`--has-bgm` 才接管）**：给了 `--has-bgm` 时，`plan` 会量这段音频的
  **能量包络**（ffmpeg `ebur128` 瞬时响度 M；不可用则退 PCM RMS）→ 拍网格 → **Bar-DP**：
  在拍位上选切点，让每 bar 的切点数跟着能量走（安静段 0–1 刀、高潮段 2 刀），
  `min_hold`/`max_hold` 仍是风格包那套硬约束（**P0-5 不新增旋钮**）。
  - bpm 来路：`--bpm 128`（曲库/人给）优先；没给就自相关估拍，此时 `bpm_source=estimated`
    且**必带 warning**（`plan` 输出与 `tmp_autoedit/beat_map.json` 都有），别当曲库数据用。
  - 上闸三条：`--has-bgm` 没给 / `--no-beat-cuts` 显式关 / `--is-speech`（对白片切在拍上会
    切断句子）→ 一律**保持 scene-change 切点**，并在 `beat_cuts.reason` 写明原因。
  - 接管是**替换**而非并集：并上 scene 切点会把安静段又切碎，两种策略互相打架。
    逐源 `replaced/kept_scene_cuts` 回报在 `auto_edit/plan.json` 同级的
    `tmp_autoedit/beat_map.json`（`export_bundle` 排除它，所以审计行落在 `decisions.jsonl`：
    `P0-5 能量波切点接管：bpm=…（explicit/estimated）× min–maxs 硬约束`）。
  - `plan.params.bpm` 记下这次用的 bpm；与 `--target-duration` 同时给会 warn——
    `_fit_target_duration` 整体缩放镜长，切点会离开拍位。
  - **闸门关掉时会把 `tmp_autoedit/beat_map.json` 删掉**：留着就是「本次切点被能量波
    接管」的假证据，量规层会照它把 m5/m6 误标 circular（真跑踩过：`--no-beat-cuts`
    后旧产物还在盘上）。重跑一次下闸的 plan 即自动清掉。
  - **量规会因此变「自证」**：切点既然按网格+能量生成，m5（吸拍）与 m6（密度∝能量）必然达标。
    量规层（`edit_metrics`）把这两条打 `circular=true`、一行摘要标 `c`（`m5c1.0`），
    **只当回归哨兵，不作质量证据**。DP 不可行（如 bpm 都估不出）时不标 circular——
    那时指标是真证据。
- **只读铁律**：输入素材任意本地可读路径即可；输出必须落在 `auto_edit/` 或
  `tmp_autoedit/`。`report.source_integrity` 按 probe 时的 sha256 复核。

工具名 `auto_edit`（capability=`auto_edit`，独立能力族，不进任何 stage 白名单）。
硬件探测见 `system_probe`（`hardware_profile` + `can_run(T2feature)`，MVP 全部 False）。

## 规范产物

每阶段产出写入 `projects/<id>/artifacts/<name>.json`，schema 见 `montage/schemas.py`：

| 阶段 | 产物 | 关键字段 |
|------|------|----------|
| research | research_brief | 选题、风格方向、参考资料 |
| proposal | proposal_packet | 概念、工具路径、成本估算、渲染运行时锁定 |
| script | script | title、sections[]（id/narration/duration_seconds）、characters[]、structure |
| scene_plan | scene_plan | scenes[]（id/description/narrative_role/shot_language/start/end）、character_registry[] |
| assets | asset_manifest + shot_prompts | shot_prompts.shots[]（scene_id/shot_kind/visual_details） |
| assets（观察） | image_bindings | 图/首帧 ↔ 场景文字/形态/实发 `<Picture N>` 冻结绑定；v1 只写不消费，`picture_index` 取实发序号 |
| compose | **edit_decisions + render_report** | edit_decisions.cuts[]（clip_path/transition）；render_report（output_path/duration/encoding） |
| publish | publish_log | 发布元数据 |

> `edit_decisions` 是 compose 阶段的**输入产物**（先用 `compose_planner` 编译，再
> `ffmpeg_compose assemble` 读它）。`compose_plan` 是每镜特效/字幕/转场的结构化方案，
> **本轮不进 produces**（缺文件不得挡 completed）。`assemble` 支持 `edit_decisions_path`；
> cuts[].transition 定义片段间转场（cut/crossfade/fade_black/wipe/zoom_punch…），
> 带转场时用 **xfade 链**渲染（负空隙 = 转场时长），否则 concat 硬拼。
> `render_kind` 默认 `ai_clip`；图形镜取值只写契约，P6 再实现覆盖层。

## 工具使用规则

1. 生成画面/视频/配音提示词前，先 `prompt_library_retriever` 检索参考词条
   （词条只作参考，**改编后使用，禁止照抄**）。
2. **写剧本前**先检索 `prompt_library_retriever` 的 `category=screenplays`
   （公有领域剧本范式：开场建世/对峙/誓言/独白等情境模板，每条标注出处与公有
   领域依据）与 `category=scripts`（结构模板：三幕/英雄之旅/人物弧光/场景三要素）。
   剧本必须原创，词条只提供范式与节奏参考（要素槽见 `scripts/elements-*`）。人物卡/分场/对白预算按
   `docs/DIRECTOR_GUIDE.md` 填写；写完后跑 `script_validator`
   （`purpose=completeness` 为 warning 级要素检查；`video_loop=jimeng` 时启用 5/10s 时长网格；
   `video_loop=agnes` 时建议 4–12s，超过 12s 请拆镜）。
   分镜阶段先跑 `script_to_scene_plan` 拿骨架，再精修；已有精修默认不覆盖。
3. assets 阶段先跑 `shot_runner`（默认 `dry_run=true`，超 `budget_ceiling_usd` 不打 API）。
   确认后再 `dry_run=false`：定妆照/首帧/质检/图生视频一次编排，写出 `shot_prompts` 与
   `asset_manifest.reference_assets`。不要绕过它手搓选型器，除非只要 Ken Burns 空镜。
   **`video_loop=agnes`**：先跑完全部静图（定妆/道具/每镜首帧，manifest 必须留公网 `url`），
   再跑视频；`audio_source=agnes_prompt`（台词进视频提示词，**不要** TTS，**不要**
   `place_audio` 叠 BGM）。默认模型 `agnes-video-2.5-flash`（1 RPM）；`AGNES_VIDEO_MODEL=agnes-video-v2.0`
   才回滚 2.0。参考必须是 http(s) URL，本地 png 不能当关键帧。
   限流/配额按 `AGNES_ACCESS_TYPE`（`default`/`enterprise`/`tokenplan`，不设即免费档）取值：
   视频实际 RPM 1/2/5，图片按 1K/2K/3K/4K 分档；Token Plan 另有每日 4000 张图 / 500 秒视频配额。
   仓库只统计 + 告警（`~/.montage/agnes_usage.json`），不阻断出片；排片提示见 `doctor` 与 dry_run `pacing_note`。
   逐镜头提示词仍可用 `visual_prompt_builder`（`purpose=shot` 产出首帧图 + 视频动态双提示词；
   `purpose=portrait|scene_ref` 产出定妆照/场景参考图）。
4. 配乐先 `soundtrack_planner`（写 `soundtrack.json`，建议 `resolve=true`，钉选曲复制进项目 `assets/music/`），再 `compose_planner` 编译 `edit_decisions`，静图 `realize=true` 之后才 `place_audio`。
   **成片主路径**是 `python -m montage produce <项目目录>`（前提：磁盘上已有 clip），不要手搓 50 个工具。换货：`asset_retriever remote=true`。CC-BY 必须署名（导出包 `CREDITS.txt`）。Agnes 片内音轨路径不要再叠 BGM。
   配音：`voice_director` 分配音色后 `tts_selector`（`synthesize=true` 才合成）。
5. **素材分析**：`video_analyzer`（分辨率/fps/编码/音轨）、`scene_detect`（镜头切点）、
   `frame_sampler`（抽帧审阅）、`audio_probe`/`audio_energy`（音轨信息/响度电平，
   混音前先查电平）、`downloader`（素材 URL 落盘，可 verify）。
   **剪现有素材先过 `scene_pipeline`（V41 P0-2）**：把 `scene_detect` 的视觉碎切
   聚成**情节单元**（8x8 RGB 指纹判换场 × 台词连读，或 `grouping=vlm` 窗口问 VLM），
   写 `artifacts/scene_index.json`。之后 `auto_edit plan` 会自动读它，把单元边界并进
   断镜点（被风格包 `min_hold` 吃掉时回报 `scene_index.dropped`，不静默成功）。
   LLM 只允许回**索引**，时间戳一律由切点推导。
6. 所有 API 调用先 `estimate_cost` 记账，执行后 `settle`；重要决策写入
   `DecisionLog`（append-only，(category, subject) 最新覆盖展示）。
7. **多角色评审与时长闭环（新增）**：角色审停点（await_setup/outline/design/shots/
   final_prompt/frames/clips）每轮 REVISE 用 `review_logger` record（role/subject 按
   `docs/ROLES.md` 枚举表；返修轮 `phase=revise`，子 Agent 关卡首审 `phase=first_pass`
   不占额度）；`operation=summary` 出轮次/振荡/role 级额度合计；`operation=metrics`
   算 DIRECT 客观量规（m1/m2/m5/m6+身份漂移，写 `artifacts/edit_metrics.json` 并落
   `edit_director`/`edit_plan` 一行，findings 带 metric/value/threshold）供 assemble
   前人审。时长问题用
   `duration_advisor`（`estimate` 双路径逐场估时+建议 / `recommend` 类型节奏锚点）。
8. **项目随行文件（V39+）**：`montage init` / 集物化自动复制 `PROGRESS_TRACKER.md`
   （静态手册，禁改写重生成）与 `STATUS.md` 手账骨架（唯一手写可覆盖文件）。
   接管项目先三读：手册 → `artifacts/produce_progress.json` → `STATUS.md`；
   "下一步"永远以 progress.json 的 status/next.argv 为准（手册只答怎么干）。
   收尾把跨会话值得留的决策写 STATUS.md（≤5 行/停点）。
   **导出包边界（V44）**：`export_bundle` 不收这两份随行文件（与 REVIEW.md 同待遇），
   交付第三方前须手动补或改 glob；老项目缺随行文件属正常（V50），按需从
   `docs/PROJECT_TEMPLATE.md` 手动补复制。

## 知识资产层（开源库）

| 资产 | 位置 | 检索方式 | 用途 |
|------|------|----------|------|
| 中文词库（170 条/12 类） | `prompt_library/` | `prompt_library_retriever` | 场景/镜头/光线/风格/人物锚点/口吻范式 |
| 公有领域剧本范式（12 条） | `prompt_library/screenplays/` | 同上 + `category=screenplays` | 剧本情境/对白/结构参考 |
| 故事结构模板（scripts 类） | `prompt_library/scripts/` | 同上 + `category=scripts` | 三幕/英雄之旅/人物弧光 |
| 音效索引（Sonniss GDC） | `assets/sfx/INDEX.md` | `asset_retriever`（category=sfx） | 环境音/动作/转场音效 |
| 音乐索引（FreePD/incompetech） | `assets/bgm/INDEX.md` | `asset_retriever`（category=bgm） | BGM 情绪/节奏（BPM 卡点） |
| 原创调色 LUT（6 个 .cube） | `assets/luts/` | `asset_retriever`（category=luts）→ `ffmpeg_compose` 的 `apply_lut` | 统一调色（电影感） |
| 字幕字体指引（思源黑体） | `assets/fonts/README.md` | 人工/Agent 读取 | ASS 字幕字体 |

下载指引：`python assets/scripts/fetch_assets.py --list`；有 `source_url` 时
`asset_retriever operation=resolve`。LUT 随仓库分发无需下载；
重新生成：`python scripts/make_luts.py`。`JAMENDO_CLIENT_ID` / `FREESOUND_API_KEY`
只在 `remote=true` 换货时需要。

**调色流程**：`asset_retriever` 按风格选 LUT（如"电影感"→ `luts/teal-orange`）→
`ffmpeg_compose` `operation=apply_lut`（`lut_path` + `lut_strength` 0-1）在整片
装配后统一调色，实现跨镜头色调一致。

## 供应商锁定

中文生产建议在 proposal 阶段锁定整条闭环（图 + 视频 + 配音同一供应商），
并写入决策日志（如 `category=video_loop, subject=供应商`），避免跨厂商画风割裂。
可用供应商（国内优先，契约分级标注）：

| 能力 | 供应商（契约状态） |
|------|---------------------|
| 图片 | 即梦（确定）、万相 dashscope_image（确定）、可灵 kling_image（待联调）、Agnes（确定，`agnes-image-2.5-flash` 单模型：文生/编辑/多图合成；免费期计 0） |
| 视频 | 即梦（确定）、万相 wan_video（确定）、智谱清影 cogvideo_video（确定）、可灵 kling_video（待联调）、混元 hunyuan_video（待联调）、Agnes（确定，默认 2.5 Flash） |
| TTS | 豆包（确定，字符级时间戳）、Edge TTS（免费）、**piper 离线**（本地，可商用，需装模型）、DashScope TTS（待联调） |
| ASR | DashScope（确定，词级时间戳） |
| 音乐/音效 | music_gen（接口占位）；现成曲目用 asset_retriever（INDEX 钉选 + 可选 Jamendo/Freesound `remote=true`） |
| 素材下载 | downloader（URL→本地，可校验） |
| 画质增强 | upscaler 超分（realesrgan 优先/ffmpeg 降级）、bg_remover 去背（rembg 优先/chromakey 降级）、face_restorer 人脸修复（gfpgan 可选） |

选型器（image_selector/video_selector/tts_selector）自动路由到已配置的供应商；
未配置密钥的工具状态为 NEEDS_CONFIG。`python -m montage doctor`（加 `--json`）
除工具菜单外列出 `video_surfaces`（Seedance 2.5 / Kling Omni / Agnes 2.0 等 API 面）。
方舟视频工具是 `seedance_video`（`ARK_API_KEY`，`provider=ark`），**不会**因为配了方舟 Key 就替换 `video_loop=volcengine` 的即梦 v30。
切 Seedance 2.5：proposal 写 `video_loop=ark`（或 `seedance`），并配置 `ARK_API_KEY` + `SEEDANCE_MODEL`。定妆图仍走即梦视觉智能。
切 Kling Omni：`video_loop=kling`，默认 Omni（路径即模型，不靠 `KLING_OMNI_MODEL`）；`KLING_FORCE_V1=1` / `KLING_FORCE_21=1` 才降级 v1 / 2.1 Pro。对白镜 `sound=on`。
Agnes 2.0 提示词路径不变（passthrough）。片内音（`agnes_prompt` / `jimeng_prompt` / `kling_prompt`）不要 TTS、不要 `place_audio` 盖对白。
密钥写在仓库根 `.env`（模板 `.env.example`），只对 CLI 入口生效。

**镜头分层成本策略（省钱提质）**：不是所有镜头都要视频生成——
- `hero_moment` / `climax` / 强运动镜头 → 视频生成（即梦/Agnes）；
- **过渡/空镜/环境镜头 → 静态图 + Ken Burns**（`ffmpeg_compose` `operation=ken_burns`，
  `zoom=in|out`、`pan=left|right|center`，可叠加环境音），成本趋近于零、效果接近。
- 一部 10 镜短片若有 5 个过渡/空镜走静态路径，预算可省 ~90%。

**素材质量门禁（坏素材不进成片）**：assets 阶段每次生成后跑
`asset_quality_gate`（黑帧/白帧 = critical 必须重生成；模糊/时长 = warning）；
A/B 选择用 `asset_picker`（锐度 + 分辨率评分选最优）；重跑不重复付费用
`generation_cache`（按 prompt+provider+seed 哈希缓存，`put` 后同参数 `get` 命中）。`--retry` 点名的镜 **不读缓存**，避免把旧片再交一次。

**成片健康（发布前）**：produce 在打 zip **之前**跑 `film_health`（ffprobe 整片：存在/视频流/时长）。critical 挡 `export_bundle`；`--skip-export` 与 `MONTAGE_RELAX_GATES=1` 不挡。不要对成片再跑 `asset_quality_gate` 的模糊检测。

**film_health 长片指标（P0-7a）**：critical 永远只报确定性硬伤，长片指标一律 warning：
- 时长口径：目标 ≥300s（或 bible 带 `chapters`）自动收紧到 10% 容差，短片仍 20%；`duration_check` 落盘实值（delta/tolerance/longform），输入 `duration_tolerance` 可显式覆盖（0=只报数）。
- 段间响度一致性：复用 P0-5 能量包络（ebur128 优先、PCM 兜底）按 60s 分块，落差 >6LU、单块偏离中位 >4LU 逐条 warning（最多 3 条+汇总）；ebur128 是绝对 LU、PCM 是相对 dB，`audio_source` 必看，跨片比较只认 LU。
- 镜连续性抽检：`continuity_samples` **默认 0（零 API）**，给点数才按 `scene_index` 锚点抽帧走 VLM（上限 12，超出截断并 warning）；结果永不进 critical——VLM 是指路（哪段该重抽），不是闸门。
- 成片路径回落：`renders/final.mp4` 缺则回落 `auto_edit/final.mp4`（`path_source` 标明来源）——剪辑现有视频的成片同样吃这套体检。

**一致性（生成后）**：`shot_runner` 在确定性质量门之后跑 `vlm_reviewer`（千问 VL）。缺 `DASHSCOPE_API_KEY` 则跳过、不挡成片。VLM critical（如定妆黑发、成片金发）进 `retryable_ids`，并让机器收口跳过 assets。场记写在 `artifacts/continuity.json`；即梦/可灵方言会带一行「场记」，Agnes 2.0 只把定妆/四视图 URL 放进关键帧，不改提示词。

**vlm_reviewer 分段抽帧（P0-7b）**：`mode="video_clip"` 现在真的抽多帧了——整段等距取 3 点（每段中点，避开首尾黑场），显式 `timestamps` 可指名抽帧位置，`max_frames` 截断（默认 3）。多帧会提示 VLM「跨帧不一致要报人物不一致」。返回多 `sampled`（抽样点/失败点）；不传参仍是单帧老行为，shot_runner 零改动。「禁止据回复填 retake_segment」不变。

**返工**：`--retry sh01` 仍是唯一入口。Seedance 2.5 / Kling Omni 在成片还有公网 URL 时走 edit/extend（不重编好段）；否则整镜重抽。ffmpeg `retake_segment` 仅当镜头写了 `retake_segment` 时间范围。不要新 CLI。

**听感专业度**：`ffmpeg_compose assemble` 默认打开 `ducking`（旁白触发式音乐闪避）与
`loudnorm`（-14 LUFS）。直接调 `mix_audio` 仍默认关闭（向后兼容）。clip 上已叠 SFX
时传 `mix_source_audio=true`。配音先 `voice_director`（对白优先镜头
`audio_prompt.dialogue`，回退 `script.lines[]`），再 `tts_selector`；BGM/SFX 用
`soundtrack_planner` 出时间轴，`place_audio` 叠到各 clip，assemble **不**再解析第二套时间轴。
**例外（Agnes）**：`audio_source=agnes_prompt` 时跳过配乐叠加与旁白混音，assemble
`mix_source_audio=true`、`ducking=false`，只保留片内音。

**字幕**：produce **finish** 默认把 `compose_plan` 里已是绝对时间的 cues 写成 `renders/final.srt`（不再偏移）。`--burn-subs` 才烧进像素；缺字体不 fail。不要把 `subtitle_builder` 放进 publish.tools。单步调试仍可用 `subtitle_builder` → `ffmpeg_compose burn_subtitles`。

**剪辑特效**（`ffmpeg_compose` operations，自创实现见 `montage/compose/effects.py`）：
- `normalize`：参数归一化（分辨率/fps 统一，拼接前必做）
- `spatial`：空间布局（`layout=side_by_side|vertical_stack|picture_in_picture`，PiP 用 `pip_position`）
- `blend_layer`：混合图层（`blend_mode=screen|overlay|multiply|add|softlight|hardlight|dodge|burn...` + `blend_opacity`）
- `speed`：变速（`speed_factor`，>2x/<0.5x 自动串联 atempo）
- `showcase_card`：9:16 展示卡片（letterbox + `title` 标题，可传 `fontfile`；**不是** 2s 片头）
- `title_card` / `lower_third`：produce finish 的 2s 片头（静音音轨）与片头结束后 4s 花字；只认 `script.title`
- `cut_silence`：静音剪切/跳切（`silence_action=remove|mark`，`silence_threshold_db`/`silence_min_duration`）
- `auto_reframe`：智能重构图（`reframe_target=9:16|16:9|1:1|...`，`reframe_mode=center|face`；face 模式 OpenCV 可选，缺失自动降级 center）
- `assemble` 的转场已带**音频 acrossfade**（片段全有音轨时，转场处声音同步交叉淡化）

**后期特效（P0-8，`montage/compose/effects.py`）**：`impact_flash`（eq 时间窗亮度脉冲）/
`zoom_punch`（zoompan d=1 急推回弹）/`camera_shake`（crop x/y 正弦抖动）+ `apply_post_vfx`
分发器（按 onset 链式；`MONTAGE_NO_VFX=1` 一键直通）。三个特效**时长守恒**（不改输出时长，
保护 film_health duration_check）。数据流：特效指导写 `bible.scenes[].shots[].vfx[]`
（唯一事实源）→ compile 自审（layer/post kind 白名单/onset 越界/密度红线/sfx 同步，
全 warning）→ compose_planner 透传 `cuts[].vfx` → assemble 拼接前逐 cut 应用。
**顺序定死：先 vfx 后 LUT**（闪白被统一调色）。提示词层走【特效】段
（`_SECTION_ORDER`/动态专属/压缩优先级三处已注册；静态首帧不带——特效是时间点事件）。
`vfx_director` 角色细则见 `docs/VFX_DIRECTOR.md`。

**风格锁定**：proposal 阶段同时从 `montage/playbooks/` 选一个 playbook
（`get_playbook(name)`，7 个内置：chinese_elegance / cyberpunk_neon /
healing_japanese / documentary_restraint / anime_shonen / manga_panel /
spoken_explain），写入决策日志
（`category=style, subject=playbook`）。未传 `style_context` 时，
`visual_prompt_builder` 从 `proposal_packet.playbook` 自动注入。显式传入则不覆盖。

**交付锁定**：proposal 阶段确定目标平台档案（`montage/compose/profiles.py`：
youtube_landscape / douyin_vertical / bilibili_horizontal / wechat_vertical /
cinematic_21_9 / youtube_4k）。produce **finish** 仅在 `proposal_packet.output_profile`
或 `--profile` 时调用 `apply_profile`；禁止只传 `project_dir` 去填管线 `default_profile`。produce finish **只**在 `proposal_packet.output_profile` 或 `--profile` 时调用，禁止只传 `project_dir` 去填管线默认档案。

## 阶段自审（门禁前）

每个 gated 阶段 checkpoint 前按 `docs/REVIEWER.md` 自审：schema 校验 →
确定性工具（`script_validator` / `edit_advisor`）→ playbook quality_rules →
严重度分级决策（critical 必带修复方案，最多 2 轮 REVISE，之后 PASS WITH
WARNINGS 兜底）。审查记录写入决策日志（`category=review`）。

## 成本与预算

- `BudgetLedger`：估算（estimate）→ 结算（settle），append-only JSONL，`projects/<id>/cost.jsonl`。
  - settle 为**追加 settlement 行**（关联 estimate_id，不反写原行——审计语义）；
    `totals()` 返回 `estimated_raw_usd` / `settled_usd` / `estimated_outstanding_usd` / `by_category`。
  - 可选 `budget_ceiling_usd` 封顶：写在 `proposal_packet` 后，`python -m montage run`
    超预算硬停（USD 为近似值）。`MONTAGE_BUDGET_SOFT=1` 可继续。
    `estimate_checked()` / `over_budget()` 仍可供直接调账本时决策。
- 看板（`montage webui`）实时展示估算/实付总额与决策记录。

## 快速命令

```bash
python -m montage doctor                          # 先填仓库根 .env；环境自检（工具/密钥/ffmpeg）
python -m montage doctor --pipeline cinematic     # 打印导演技能路径
python -m montage tools                           # 列出全部工具
python -m montage init <id> --title "项目名"       # 初始化项目
python -m montage status <项目目录>                # 阶段状态
python -m montage check <目录> <stage> --completed --approved   # 写 checkpoint（门禁+产物校验）
python -m montage check <目录> <stage> --caps analysis,prompt_engineering --caps-strict
python -m montage run <目录> <工具名> --input inputs.json       # 执行工具（stdout 一行 JSON）
python -m montage produce <项目目录>                  # 已有 clip 拼片；缺 clip 时 cinematic/documentary 先生成
python -m montage produce <项目目录> --resume         # 只跑未成功的步骤；系列根：样品后续本集，await_episode 才下一集
python -m montage produce <项目目录> --idea "讲量子计算"  # W1：默认导演档，cascade 后停 await_setup（尚未编译），不生成；--review bible 才停 await_bible
python -m montage produce <项目目录> --tts            # W2：生成后才合成对白
python -m montage produce <项目目录> --profile douyin_vertical  # finish 显式平台档案
python -m montage produce <项目目录> --retry sh01 --yes       # 重跑指定镜（先 --retry 预览费用）
python -m montage produce <系列目录>/episodes/ep02    # W3：只续这一集
python -m montage auto_edit <目录> --video raw.mp4 --style documentary     # AutoEditor 快速路径（不进管线）
python -m montage webui --port 8399               # 启动看板
```

看板系列根只播已有 `renders/season.mp4`，不在根上拼季；季片不是默认产物（须 `--season-concat`）。子集 / 扁平仍播 `final.mp4`。导演档停点会显示折叠确认卡（摘要默认，展开后改 bible/scene_plan 再 CLI `--resume`）；看板**不会**一键出片。保存走带 id 的叶子（`bible.characters[a].appearance`、`scene_plan.shots[sh01].shot_language.camera_movement=dolly_in`）；通配 `scenes[].shots[]` 和中文「慢推」都不会写盘。

**SSE 实时刷新（P0-7c）**：看板经 `/api/events` 收 SSE——artifacts/decisions/cost/成片/plan_history 一变（mtime+size 指纹）就推 `changed`，前端照旧走既有 GET 重画。只刷视图，**不放行写操作**；review 表单展开或 `<video>` 播放中不重画（防冲掉输入/打断播放），改挂「有更新 · 点我刷新」。事件只带 status/next 不塞 detail；`max_idle` 防连接泄漏（EventSource 自动重连）。

## 外壳与出错率（经验，非 SLA）

成片走 `python -m montage produce`（Cursor Skill：`.cursor/skills/montage-produce/SKILL.md`）。`await_*` 是约定停点（code=0），不是失败，也不是人审。本仓库用停点 + Skill 导航，不实现 ReAct / CoT 运行时。

以下是操作经验，**不是**可用性承诺、精确百分比或 SLA：

- 零密钥 + 本机 ffmpeg、磁盘已有 clip：W0 拼片通常能写出 `renders/final.mp4`
- 真密钥图生视频：样品常漂、常要 `--retry`；跨镜一致性靠定妆 URL + 场记 + 可选千问 VLM（无 `DASHSCOPE_API_KEY` 则跳过）
- `MONTAGE_HEADLESS=1` 会在样品停 / 未确认 retry / 系列非 `--review none` / 未确认 `await_final_prompt`（含 `--review none`）时进门失败，避免把停点当成成功

### ffmpeg 版本矩阵（换机器必先看这节）

敏感点不做人肉排障：`montage/compose/ffmpeg_compat.py` 对三项能力**真跑一次**探测并进程内缓存。

| 敏感点 | 探测键 | 现代写法 | 旧版/回落 | 使用处 |
|--------|--------|----------|-----------|--------|
| LUT 选项名 | `lut3d_file` | `lut3d=file=<path>` | `filename=` | `ffmpeg_engine.lut3d_filter` |
| 声道归一到 48k 立体声 | `aformat` | `aformat=sample_rates=48000:channel_layouts=stereo` | `aresample=48000:ochl=stereo` | `ffmpeg_engine._audio_norm_filter` |
| xfade 硬切 | `xfade_cut` | 调用方把 cut 拆成 concat | `xfade` 无 `cut`（9.x 直接失败） | `assemble` 转场链 |

- 探测结果**只反映本机**、不跨进程缓存；缺 ffmpeg 时对现代选项名返回 `True`（只构造命令字符串的单元测试不退化），真实渲染前 `check_ffmpeg()` 仍会拦。
- `render_report.json` 带 `ffmpeg_version` + `ffmpeg_capabilities` 快照，事后可归因；本机（gyan build）实测：`9.0-full_build`，`lut3d_file/aformat=True`、`xfade_cut=False`。
- 回归兜底在 `tests/test_ffmpeg_compat.py`（lavfi 生成素材、真跑、`probe()` 断言语义）：`python -m pytest -m ffmpeg` 可单独筛，`scripts/minitest.py` 会显式报告跳过数而不是静默通过。

### Agnes 合同陷阱（首帧/reference 互斥、720P、5 张上限、Picture 语义）

1. **首帧与参考图互斥**：`proposal_packet.frames_mode` 三选一——
   - `preview`（默认）：首帧不进生成，只当审图素材；produce 直接跳过 `await_frames`。
   - `reference_first`：首帧进 `images[0]`（`<Picture 1>` = 本镜首帧）。
   - `keyframe`：真 I2V，只发 `first_frame`/`last_frame`，`mode=keyframe`，不再叠 `aspect_ratio`；与 `images`/`audios`/`videos` 互斥。
   - 踩坑症状：`images` 非空即 `mode=reference` 并清空 first/last——首帧白花配额却对画面零影响。
2. **720P 是硬限，且分辨率非标准**：视频 `size` 写死 `"720P"`；实测 16:9 = `1280×704`（**不是** 1280×720）、9:16 = `720×1280`，竖屏成片 1080×1920 即 1.5× 上采样。图片 2K 实测 16:9 = `2624×1472`、9:16 = `1472×2624`、1:1 = `2048×2048`，同样不是 1920×1080。
   - 铁律：缩放/letterbox 一律以 ffprobe 实测为准（`render_report` 已记 `width`/`height`/`clips`/`images`）；`AGNES_V25_VIDEO_SIZES`/`AGNES_IMAGE_2K_SIZES` 只作知识兜底，`_V20_WH` 只服务已退役的 v2.0。
3. **参考图 ≤5 张、音频 ≤3 段，且必须公网 http(s)**：超限会 400 或截断；本地 png / Data URI 被丢弃并出 finding（Data URI 只在图片侧合法）。`extract_last_frame` 产出的尾帧是本地文件，Agnes 视频天然不能用。图片侧多图合成必须在提示词写明每张图的角色（图例 `【参考图角色】`）。
   - 身份图**每个出场形态只发一张**：默认 `portrait`；`turnaround` 需在 `form.cast_ref_kind` / `character.cast_ref_kind` / `proposal_packet.cast_ref_kind` 之一显式写 `turnaround`。四视图是分格拼板，参考生图会抄版面（首帧重复人物），且图例附「禁止分格/拼贴」约束——**opt-in 仍有风险**，默认用 portrait 省名额。
   - **名额不够时默认切段续拍**（`proposal_packet.ref_overflow_mode=segment`）：段是时间轴切片，段数 `max(参考组, 时长段)`、各段秒数之和不变（同一 `shot_id`），段 2 起用「上段尾帧 + 本段参考」图片侧合成 <Picture 1> 续接帧（占 1 个视频名额，故段 2+ 有效参考 = 上限−1）。硬封顶 4 段；`身份+场景` 超每段名额、时长切不动、超封顶时**不开跑**，回退丢弃+finding。`single` 一键回旧行为；`frames_mode=keyframe` 强制 `single`。桥接帧用 continuation 描述（不复用首帧提示词）。
4. **`<Picture N>` 只有一套来源**：`_agnes_flash_image_plan` 的最终有序表（rank：`first_frame`(-1) → 身份图（`identity=True`，每形态选中一张，统一 rank 0）→ `scene_ref` → `prop` → `style_anchor` → `turnaround`）。四视图**不再因多角色镜被硬丢**，只按 `cast_ref_kind` 决定是否入候选。N 是**实发顺序下标**，不是 manifest 序号；截断掉 `scene_ref`/`prop` 会出 finding；引用行与实发 `images` 必须逐下标同 kind（`tests/test_visual_prompt_builder.py` 锁住）。
5. **其余静默丢弃**：`videos[]` 对 Flash 有效内容直接 400（工具直调也不读该字段）；Agnes 片内音（`audio_source=agnes_prompt`）不要 TTS、不要 `place_audio` 盖对白。
