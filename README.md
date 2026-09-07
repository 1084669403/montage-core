# montage-core

**中文优先、Agent 驱动的 AI 视频生产系统**（独立项目，全新原创代码，MIT）。

> 定位：把"AI 视频生产"做成一条可治理、可扩展、有镜头语言的管线——
> Agent 读管线配置、调注册表工具、按 checkpoint 门禁逐步推进；
> 提示词工程是知识资产（中文剧情语义 + 英文画质层常量）。

---

## English

**montage-core** is a Chinese-first, agent-driven AI video production system (independent project, original code, MIT).

Turns a one-line idea into a finished film through a governed 7-stage pipeline (research → proposal → script → scene_plan → assets → compose → publish): an LLM agent acts as the **director** making creative decisions, while deterministic Python code acts as the **studio** enforcing schemas, budgets and quality gates.

**Highlights**

- **Agent-as-director architecture** — checkpoint state machine with JSON-Schema artifact gates, human-approval stops, append-only decision/cost ledgers, resumable runs, and a headless mode for unattended/CI use.
- **Multi-provider routing** — capability-table-driven selectors (no provider if/else) unify 10+ Chinese video/image/TTS services (Jimeng, Wanxiang, CogVideoX, Kling, Hunyuan, Agnes, Doubao TTS, Edge/Piper...) behind one auto-discovered tool registry (~58 tools).
- **Prompt dialects** — per-API-surface prompt adaptation (citation syntax, duration grids, token budgets, negative prompts) locked by golden-fixture contract tests.
- **Generation QC** — deterministic FFmpeg checks (black/blur frames) → VLM semantic review (Qwen-VL) → smart retry routing (edit/extend/regenerate), with cross-shot character continuity.
- **Cost governance** — two-layer budget control (dry-run estimate + per-call hard stop), append-only cost ledger, shot-tier cost policy (video only for hero shots; stills + Ken Burns for filler).
- **Compose engine** — pure FFmpeg: xfade transitions, Ken Burns, LUT color grading (6 original generated `.cube` LUTs), SRT/ASS subtitles, sidechain-ducked narration/BGM mixing, platform output profiles.

**Quick start**

```bash
pip install -e .          # core dependency: jsonschema only
python -m montage doctor  # see which providers are available
python examples/pipeline_flow.py   # full demo, zero API keys
python -m montage webui --port 8399 # local dashboard
```

Requires Python ≥ 3.10 and `ffmpeg`/`ffprobe` on PATH. See the Chinese sections below for the full guide (project is Chinese-first; docs are bilingual where it matters).

---

## 设计原则

1. **Agent 是导演，代码是制片厂**：编排/创意决策由执行者按 `docs/AGENT_GUIDE.md`
   与 `montage/pipelines.py` 配置驱动；Python 只提供工具与状态管理。
2. **产物即契约**：每阶段产出可校验的 JSON artifact（`artifacts/`），schema 校验失败即失败。
3. **注册表 + 选型器**：新增供应商 = 写一个 `BaseTool` 子类放进 `providers/`，选型器自动发现。
4. **全程可审计**：成本记账（估算→结算）、决策日志（append-only、最新为准）、checkpoint 历史归档。
5. **提示词工程沉淀为资产**：`prompt_library/`（170 条中文词条，12 类，含 12 条
   公有领域剧本范式与人物/口吻锚点）+ 五层镜头语言 + 首帧/视频双提示词 + 压缩预算 + 词库兜底注入。

## 能力总览

- **管线引擎**：research → proposal → script → scene_plan → assets → compose → publish，
  门禁审批、历史归档、产物校验、成本账本、决策日志；**3 条管线**（cinematic 剧情 /
  documentary 纪实 / clip_factory 切片工厂）。
- **契约治理**：独立 gates 层在 checkpoint 完成时校验 produces 产物存在+schema
  （`MONTAGE_RELAX_GATES=1` 可降级）；`check --caps` 能力族白名单；预算封顶与
  settlement append-only 对账；选型器单例路由缓存；产物原子写。
- **合成**：`compose_planner` 把每镜转场/LUT/字幕槽写成 `compose_plan` 并编译为
  `edit_decisions`（assemble **只读**后者；`compose_plan` 不进 completed 必填）；
  FFmpeg 拼接 / **xfade 转场（cut/crossfade/fade_black/wipe/负空隙）** /
  **Ken Burns 静态图运镜（zoompan）** / 裁剪 / 字幕烧录（支持 ASS 样式字幕）/
  旁白+配乐混音（assemble 默认 **ducking + loudnorm**；`mix_audio` 直接调用仍默认关）/ 整片装配 / LUT 统一调色
  （`apply_lut`）/ 平台档案出片（`apply_profile`）/ 配音装配（plan/assemble_narration）。
- **生成策略**：`shot_runner` 编排定妆照→首帧→质检→图生视频（默认 `dry_run`，
  超预算不打 API）；`asset_quality_gate` 质量门禁、`asset_picker` A/B 选优、
  `generation_cache` 生成缓存、镜头分层成本（过渡/空镜走静态图+Ken Burns）。
- **剧本质量层**：人物卡（`characters[]`）/故事节拍/角色注册表进 schema；
  `script_validator` 确定性门禁（对白预算按供应商时长网格、即梦 5/10s、可拍性
  心理词、人物引用一致性）；导演执导手册 `docs/DIRECTOR_GUIDE.md`；自审协议
  `docs/REVIEWER.md`。
- **提示词工程**：中文词库检索（零依赖确定性检索）、逐镜头"首帧图 + 视频动态"双提示词、
  剪辑转场确定性顾问、公有领域剧本范式库（screenplays）。
- **风格 playbook**（`montage/playbooks/`，Python dict 零依赖）：中文优雅/赛博朋克/
  日系治愈/纪实克制/少年漫/漫画分镜/口播讲解（7 本），proposal 锁定后贯穿全片。
- **开源资产库**（`assets/`）：音效索引（Sonniss GDC）、音乐索引（FreePD/incompetech，
  BPM 卡点）、原创调色 LUT（6 个 .cube，`scripts/make_luts.py` 生成）、字幕字体指引
  （思源黑体 OFL）；`asset_retriever` 工具零依赖检索，`ffmpeg_compose` 支持 `apply_lut`
  统一调色与 `apply_profile` 平台档案出片。
- **国产供应商闭环**（接口契约分级标注，配好密钥即用）：
  - Agnes：图片 / 视频 / 配音闭环（`AGNES_API_KEY` / `AGNES_CN_API_KEY`）
  - 即梦（火山引擎）：图片 `jimeng_t2i_v40`、视频 3.0 家族（HMAC V4 签名，`VOLC_ACCESSKEY/SECRETKEY`）
  - **万相**（DashScope）：生图 `wan2.5-t2i` / 视频 `wan2.1-t2v`（异步任务模式，`DASHSCOPE_API_KEY`）
  - **智谱清影**：CogVideoX 视频生成（`ZHIPU_API_KEY`）
  - **可灵**（快手）：图/视频生成（`KLING_API_KEY`，待联调）
  - **混元**（腾讯）：视频生成（`HUNYUAN_API_KEY`，待联调）
  - DashScope：词级时间戳 ASR（`DASHSCOPE_API_KEY`）
  - 豆包：TTS 字符级时间戳（`DOUBAO_SPEECH_API_KEY`）；Seed-Audio 音乐生成（待联调）
  - Edge TTS：免费中文配音（`pip install edge-tts`，无需密钥）
  - **Piper**：离线本地 TTS（`pip install piper-tts` + 中文模型，可商用）
- **素材与交付**：`stock_retriever` 素材源检索（wikimedia 免费 / pixabay / pexels）、
  `downloader` 落盘、`export_bundle` 项目导出包（zip + manifest）、`screen_recorder` 录屏。
- **成片收尾**：`produce` 在拼接之后跑 finish（显式 LUT / 平台档案 / 2s 片头 / 字幕旁路）
  和 `release_pack`（封面抽帧 + 三平台简介模板 + `publish_log`），再打 zip。
- **Web 看板**：项目列表、阶段进度、产物浏览、预算、决策记录、checkpoint 一键写入；
  七阶段成片播 `renders/final.mp4`（与 AutoEditor 分开）；`--retry` 二次确认后重跑指定镜。

## 快速开始

```bash
cd montage-core
copy .env.example .env            # PowerShell: Copy-Item .env.example .env
# 编辑仓库根 .env，填即梦 VOLC_ACCESSKEY/SECRETKEY 等（空值=未配置；不要 git add）
python -m montage doctor          # 看哪些供应商变成 available（直接 import 工具读不到 .env）
python scripts/minitest.py        # 需 pip install -e .（核心依赖 jsonschema）
python -m montage init <id> --title "项目名"
python -m montage run <项目目录> prompt_library_retriever --input inputs.json
python -m montage produce <项目目录>   # 已有 clip 时配乐→拼片→finish→发布包→zip
python -m montage produce <项目目录> --retry sh01 --yes  # 重跑指定镜（先预览费用）
python -m montage auto_edit <dir> --video raw.mp4 --style documentary  # 快速剪辑（不进 7 阶段）
python -m montage webui --port 8399  # 启动看板（需 pip install "montage-core[webui]"）
python examples/pipeline_flow.py     # 完整流程示例（本地确定性工具，无需密钥）
python examples/zero_key_edit.py     # 零密钥本地剪辑（需 MONTAGE_REAL_FFMPEG=1）
python scripts/make_luts.py          # 重新生成原创调色 LUT（assets/luts/*.cube）
python assets/scripts/fetch_assets.py --list   # 查看待下载的音效/音乐/字体清单
```

## 目录结构

```
montage-core/
├── montage/
│   ├── toolbase.py          # BaseTool 契约（ToolResult/状态/成本估算/输入校验）
│   ├── registry.py          # 工具注册表 + 能力选型 + 能力菜单
│   ├── schemas.py           # 规范产物 JSON Schema（精简；script 含人物卡/节拍）
│   ├── pipelines.py         # cinematic / documentary / clip_factory 管线配置
│   ├── cli.py               # doctor/tools/init/status/check/run/auto_edit/webui
│   ├── engine/              # stages / artifacts / budget / decisions / gates / runtime
│   ├── tools/               # 词库检索 / 视觉提示词 / 剪辑顾问 / 剧本校验
│   ├── providers/           # agnes / jimeng / dashscope / doubao / edge_tts / selectors
│   ├── playbooks/           # 7 个原创风格 playbook（Python dict，无 YAML）
│   ├── compose/             # FFmpeg 合成 + 配音装配 + LUT 调色 + 平台档案
│   └── webui/               # FastAPI 服务 + 单页看板
├── lib/                     # 确定性知识模块（提示词库检索、镜头提示词装配、资产目录检索）
├── prompt_library/          # 170 条中文结构化词条（12 类；MIT/公有领域，见 CREDITS.md）
├── assets/                  # 开源媒体资产库（音效/音乐/LUT/字体索引 + 许可证，见 README.md）
├── docs/                    # AGENT_GUIDE / DIRECTOR_GUIDE / REVIEWER / skills / PROGRESS
├── examples/                # pipeline_flow / zero_key_edit
├── scripts/                 # minitest / make_luts（原创调色 LUT 生成）
└── tests/                   # 见 docs/PROGRESS.md 的 minitest 计数
```

安装：`pip install -e .`（核心依赖仅 `jsonschema`）。系统需 `ffmpeg`/`ffprobe`。Web 看板：`pip install "montage-core[webui]"`。

## 契约分级（供应商接口）

- **确定**：配好密钥即可按官方契约调用 — 即梦图/视频、万相图/视频、智谱清影、Agnes 图片、DashScope ASR、豆包 TTS、Edge TTS。
- **待联调**：接口已写，需真实密钥核对 — 可灵图/视频、混元视频、Agnes 视频/配音、豆包 Seed-Audio、talking_head / lip_sync。
- **仅接口**：占位，未接供应商 — `music_gen`。

本轮不引入 Remotion / HyperFrames：产品是拼接 AI 片段（FFmpeg 已覆盖）；Node 运行时会拆仓库；若抄 OpenMontage 合成器会触 AGPL。

## 生态（Ecosystem）

montage-core 按依赖方向拆出两个可独立使用的库（行为与主仓对应层一致）：

- **[montage-providers](https://github.com/1084669403/montage-providers)** — 国产多供应商视频/图像/TTS 统一 SDK：能力表驱动路由、逐 API 面"提示词方言"、黄金 fixture 契约测试，零第三方依赖。
- **[montage-composer](https://github.com/1084669403/montage-composer)** — 纯 FFmpeg 合成工具库：xfade 转场链、Ken Burns、LUT 调色（含 6 个原创 LUT）、字幕、ducking 混音、平台出片档案，零第三方依赖。

> 演示视频（可灵生成）待补：占位 — TODO(demo video)。

## 许可证

MIT。代码为全新原创实现；`prompt_library/` 词条来源见 `prompt_library/CREDITS.md`
（MIT 开源仓库精选改写，保留版权声明）。
