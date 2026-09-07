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
```

- **三选一**：`--video` / `--clips`（多段顺序拼接，段内独立分析，不先 concat） /
  `--audio-only`（黑场 + 音轨）。
- **风格包**（`montage/style_packs.py`）：cinematic / documentary / beat / classic /
  fresh / cyber。管节奏、LUT、转场、`output_profile`；`bind_playbook` 只写入
  `plan.json` 元数据，MVP **不**调用 `visual_prompt_builder`。
- **产物**：`<project_dir>/auto_edit/{plan.json,report.json,final.mp4}`；
  中间文件与 480p 预览在 `tmp_autoedit/`（`export_bundle` 排除这两目录）。
- **缺 `project.json`**：在 `project_dir` **原地**写最小元数据，**不**调用
  `init_project`（不会落到 `<root>/projects/<id>`）。看板只扫
  `webui --root` 下的 `projects/<id>`，任意目录快速剪辑能出片但默认不出现在看板。
- **人机闭环**：改 `plan.json` 后用工具 `operation=replan` 或 CLI
  `--overrides '{"seg_0":{"action":"drop"}}'`；决策日志
  `category=auto_edit, subject=auto_edit/<project_id>/<session_id>`，本地 ffmpeg 账本 $0。
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
   逐镜头提示词仍可用 `visual_prompt_builder`（`purpose=shot` 产出首帧图 + 视频动态双提示词；
   `purpose=portrait|scene_ref` 产出定妆照/场景参考图）。
4. 配乐先 `soundtrack_planner`（写 `soundtrack.json`，建议 `resolve=true`，钉选曲复制进项目 `assets/music/`），再 `compose_planner` 编译 `edit_decisions`，静图 `realize=true` 之后才 `place_audio`。
   **成片主路径**是 `python -m montage produce <项目目录>`（前提：磁盘上已有 clip），不要手搓 50 个工具。换货：`asset_retriever remote=true`。CC-BY 必须署名（导出包 `CREDITS.txt`）。Agnes 片内音轨路径不要再叠 BGM。
   配音：`voice_director` 分配音色后 `tts_selector`（`synthesize=true` 才合成）。
5. **素材分析**：`video_analyzer`（分辨率/fps/编码/音轨）、`scene_detect`（镜头切点）、
   `frame_sampler`（抽帧审阅）、`audio_probe`/`audio_energy`（音轨信息/响度电平，
   混音前先查电平）、`downloader`（素材 URL 落盘，可 verify）。
6. 所有 API 调用先 `estimate_cost` 记账，执行后 `settle`；重要决策写入
   `DecisionLog`（append-only，(category, subject) 最新覆盖展示）。

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
| 图片 | 即梦（确定）、万相 dashscope_image（确定）、可灵 kling_image（待联调）、Agnes（确定） |
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

**一致性（生成后）**：`shot_runner` 在确定性质量门之后跑 `vlm_reviewer`（千问 VL）。缺 `DASHSCOPE_API_KEY` 则跳过、不挡成片。VLM critical（如定妆黑发、成片金发）进 `retryable_ids`，并让机器收口跳过 assets。场记写在 `artifacts/continuity.json`；即梦/可灵方言会带一行「场记」，Agnes 2.0 只把定妆/四视图 URL 放进关键帧，不改提示词。

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

## 外壳与出错率（经验，非 SLA）

成片走 `python -m montage produce`（Cursor Skill：`.cursor/skills/montage-produce/SKILL.md`）。`await_*` 是约定停点（code=0），不是失败，也不是人审。本仓库用停点 + Skill 导航，不实现 ReAct / CoT 运行时。

以下是操作经验，**不是**可用性承诺、精确百分比或 SLA：

- 零密钥 + 本机 ffmpeg、磁盘已有 clip：W0 拼片通常能写出 `renders/final.mp4`
- 真密钥图生视频：样品常漂、常要 `--retry`；跨镜一致性靠定妆 URL + 场记 + 可选千问 VLM（无 `DASHSCOPE_API_KEY` 则跳过）
- `MONTAGE_HEADLESS=1` 会在样品停 / 未确认 retry / 系列非 `--review none` / 未确认 `await_final_prompt`（含 `--review none`）时进门失败，避免把停点当成成功
