# 项目进度（已完成）

> montage-core：中文优先、Agent 驱动的 AI 视频生产系统（MIT，全新原创）。

## 交付总览

| 阶段 | 内容 | 状态 |
|------|------|------|
| P0 | 管线引擎（状态机/checkpoint 门禁/历史归档/产物校验/成本账本/决策日志/项目工作区）+ BaseTool 契约 + 注册表/选型 | ✅ |
| P1 | 迁移用户原创 `prompt_library`（132 词条/9 类）+ 词库检索/视觉提示词/剪辑顾问工具 + schema + 管线配置 + CLI | ✅ |
| P2 | 国产供应商闭环：Agnes（图/视频/配音）、即梦（图/视频，HMAC V4）、DashScope（词级 ASR）、豆包（TTS 时间戳）、Edge TTS + 三选型器 | ✅ |
| P3 | FFmpeg 合成引擎（拼接/裁剪/字幕/混音/整片装配）+ 配音装配 | ✅ |
| P4 | FastAPI + 单页 Web 看板（项目/阶段/产物/预算/决策/checkpoint） | ✅ |
| P5 | docs/AGENT_GUIDE + examples + README + git 历史（5 个阶段提交）+ 测试全绿 | ✅ |
| P6 | 开源知识资产层：公有领域剧本范式库（screenplays 12 条）+ scripts 结构模板扩展（+5 条，词库共 149 条/10 类）+ assets/ 资产库（音效 Sonniss / 音乐 FreePD·incompetech / LUT / 字体思源）+ 原创 LUT 生成器（6 个 .cube）+ `asset_retriever` 检索工具 + `apply_lut` 统一调色 | ✅ |
| P7 | 借鉴 OpenMontage 思路（原创实现）：script/scene_plan schema 扩展（人物卡 characters[]/故事节拍/character_registry）+ `script_validator` 剧本质量门禁（对白预算/即梦 5·10s 网格/可拍性/人物引用）+ 4 个原创 playbook + 平台渲染档案 `compose/profiles.py` + `apply_profile` + `docs/DIRECTOR_GUIDE.md` 执导手册 + `docs/REVIEWER.md` 自审协议 | ✅ |
| P8 | 契约治理强化：独立 `gates.py` 产物门禁（checkpoint 完成时校验 produces 存在+schema，只拦 completed、relax 降级、不回溯历史）+ `check --caps` 能力族白名单 + `edit_decisions` 归 compose produces 且 `assemble` 支持从 artifact 读取 + **ffmpeg xfade 转场执行闭环**（cut/crossfade/fade_black/wipe/负空隙）+ 预算封顶与 settlement append-only 对账 + 配音装配工具化（plan/assemble_narration）+ 选型器单例/原子写/doctor 告警 jsonschema + 即梦首尾帧字段与 seed 落盘 + DIRECTOR_GUIDE 一致性强制流程 | ✅ |
| P9 | 生成策略与音频增强：Ken Burns 静态图运镜（`ken_burns` zoompan）+ 镜头分层成本策略 + `asset_quality_gate` 质量门禁（黑帧/白帧/模糊/时长）+ `mix_audio` ducking（sidechaincompress）与 loudnorm 响度标准化 + `subtitle_builder`（时间戳→SRT/ASS 样式字幕）+ `asset_picker` A/B 选优 + `generation_cache` 生成缓存 | ✅ |
| P10 | OpenMontage 能力迁移（自创实现，规避 AGPL）：`docs/ROADMAP.md` 迁移路线图 + M1 剪辑特效增强——`montage/compose/effects.py`（参数归一化 normalize / 空间布局 spatial 分屏·竖排·画中画 / 混合图层 blend_layer / 变速 speed / 展示卡片 showcase_card / 静音剪切 cut_silence / 智能重构图 auto_reframe）+ `assemble` 转场音频 acrossfade | ✅ |
| P10 | M2 分析工具补齐（自创）：`frame_sampler` 抽帧 / `scene_detect` 场景切点 / `video_analyzer` 视频综合 / `audio_probe` 音轨信息 / `audio_energy` 响度电平 / `downloader` 素材下载（可校验） | ✅ |
| P10 | M3 国内供应商补齐（自创，官方契约）：万相生图 dashscope_image / 万相视频 wan_video（确定）+ 智谱清影 cogvideo_video（确定）+ 可灵 kling_image/kling_video（**接口在、未联调**）+ 混元 hunyuan_video（**接口在、未联调**）+ piper 离线 TTS + music_gen（**接口占位、未联调**）+ 豆包 seed_audio（**接口在、未联调**） | ✅ 接口 |
| P10 | M4 增强工具接口（可选依赖+降级）：`upscaler` 超分（realesrgan-ncnn-vulkan 优先 + ffmpeg lanczos 降级）/ `bg_remover` 去背（rembg 优先 + chromakey 绿幕降级）/ `face_restorer` 人脸修复（gfpgan 可选，缺失明确报错） | ✅ |
| P10 | M5 数字人/录屏：`screen_recorder` 录屏可用；`talking_head` / `lip_sync` **接口在、未联调** | ✅ 接口 |
| P10 | M6 素材源与导出：`stock_retriever` 素材检索（wikimedia 免费无 key / pixabay / pexels，返回许可证）+ `export_bundle` 项目导出包（成片+产物+账本+素材清单 → zip + manifest） | ✅ |
| P10 | M7 管线扩展：新增 `documentary`（纪实克制）与 `clip_factory`（切片工厂，scene_detect 定位切点）两条管线（同阶段序列，gated/produces/tools 差异化）+ ROADMAP 终稿 | ✅ |
| P11 | AutoEditor MVP（快速路径，不进 7 阶段）：`system_probe` 硬件档案 + 6 个 StylePack + `auto_edit` 编排器（probe/analyze/plan/preview/replan/render）+ CLI `auto_edit` + 看板回看 `/media` + `export_bundle` 排除中间目录 | ✅ |
| P12 | 契约闭环：jsonschema 进核心依赖；discover 含 compose；补齐 research_brief/proposal_packet/asset_manifest/publish_log/clip_plan schema；`run_tool` + `montage run --input`；图/视频供应商锁定不套 TTS；预算硬停；三条管线顶层字段被 assemble/advisor 消费；导演技能页；`extract_last_frame`；零密钥演示 | ✅ |
| S0–S5 | 生产流水线能力层（见 `docs/ROADMAP.md` S 系列）：剧本契约 → playbook/matcher → script_to_scene_plan → shot_runner → 音频时间轴 → **compose_plan 编译 edit_decisions**（不进 produces） | ✅ |
| W0 | 已有分镜片段一条 `montage produce`：配乐→编译→Ken Burns→叠音→拼接→打包（不写剧本、不调 shot_runner、不改人审） | ✅ |
| W1 | 想法→级联 `format_card`；无圣经 `need_bible`；有圣经 `produce --idea` 停 `await_bible`（尚未编译）；第二次 produce 才校验+编译（不生成） | ✅ |
| W2 | 生成接入 produce：缺 clip 时 cinematic/documentary 先 dry_run→shot_runner（定妆失败禁 I2V）；`--idea` 仍不生成。时长表 2a；衔接 2b：尾帧做下一镜 I2V 首帧，cut=hard / location_id 才清桥。默认样品停 `await_sample`，`--resume` 全量；`approved_by=produce` 仅机器收口 assets/compose/publish | 波次1 ✅ 波次2a ✅ 波次2b ✅ 波次3 ✅ |
| W3 | 长片分层：缺 class 默认 talk（静图+Ken Burns）；hero 才 I2V；pending hero >30% 停；`--trim-hero` / `--all-video`；hero 全量才填即梦 last_frame。多集：`episodes.json` 物化子集，resume 粒度=集；WebUI 列出子集（W4 起子集可写 checkpoint；机器收口 ≠ 人审） | 波次1 ✅ 波次2 ✅ |
| W4 | 能看、能发：finish + release_pack；看板播 `renders/` 与 retry；切片厂配乐并打包 | 波次1 ✅ 波次2 ✅ 波次3 ✅ |
| W5 | Harness 外壳只调 produce：项目 Skill + `progress.next` + `MONTAGE_HEADLESS` 进门拒绝 + `doctor --json`；看板仍无一键全片 GEN | 波次1 ✅ 波次2 ✅ 波次3 ✅ |
| W6 | 系列交卷：系列可 opt-in 拼季 / 跨集拷定妆 / 勾选 retry | 波次1 ✅ 波次2 ✅ 波次3 ✅ |
| P-D0 | 导演档 `--review director`：idea 侧 `await_setup` → `await_outline` → `await_design` → compile → `await_shots`（不 GEN） | ✅ |
| P-D1 | 定妆提前：design 过完、compile 前出全身照+四视图，停 `await_cast` | ✅ |
| P-D2 | 切开关键帧：`await_shots` 后只出首帧停 `await_frames`；人过后再 I2V / `await_sample` / `await_clips` | ✅ |
| P-D3 | 生成前硬停点：`await_frames` 全过后先进 `await_final_prompt`（最终提示词总览，`stage=prompt_preview` 复用真实构建路径、不落盘媒体）；CLI 默认 `--review director`；`--review none` 未过 `await_final_prompt` 前也强制停；headless 遇未确认停点 fail | ✅ |
| P0（进化） | 契约地基：`VIDEO_SURFACES`（2.5/Omni/Agnes2.0）与当前接线分表；`video_prompts` + adapter（Agnes passthrough）；`doctor --json` 列出 API 面；`video_loop=kling` | ✅ |
| P1（进化） | 适配器：方舟 `seedance_video` + Kling Omni/2.1；专用注入器；不改 volcengine 默认选型 | ✅ |
| P2（进化） | 公共路由：`_route_shot` 按环家族选 API 面；`video_loop=ark` 走 Seedance 2.5（volcengine 仍 v30）；adapter 进 shot_runner；`keep_embedded_audio` 认片内音 | ✅ |
| P3（进化） | 手搓一致性：消费四视图 URL；场记 sidecar；千问 VLM 在确定性门之后；Agnes 只加关键帧不改 prompt | ✅ |
| P4（进化） | 导演语言：节拍→运镜（`shot_language.py`）只补空；`purpose=composition` / `beat_coverage`；不改 Agnes 提示词模板 | ✅ |
| P5（进化） | 返工与健康：`--retry` 优先 Seedance edit/extend、Kling Omni `video_list`；无 URL 整镜重抽；`film_health` 挡 export；`retake_segment` 兜底 | ✅ |
| P6（进化） | 交付：看板折叠确认卡；带 id 叶子可保存（运镜英文 id）；不 exec produce；无一键 GEN | ✅ |
| 可灵 Omni 第 0 波 | 官方契约原语：双 base / 装箱 / 三分 poll；execute 默认仍 v1 | ✅ |
| 可灵 Omni 第 1 波 | 四分视频 execute + golden（Omni/t2v/i2v/motion）；credits×汇率；生产路由未切 | ✅ |
| 可灵 Omni 第 2 波 | KlingImage → Image Omni（默认 2k）；Element/Voice 内部 CRUD；拼板裁切+QC 原语；未接 shot_runner | ✅ |
| 可灵 Omni 第 3 波 | 注入器 contents[] 互斥；`@image_N` 跳过首尾帧；`build_kling_prompt` + 拼板 appearance；默认路由未切 | ✅ |
| 可灵 Omni 第 4 波 | 生产接线：`_pick_kling` 默认 Omni；`policy_for_loop` 3–15；可灵导演 shots→cast；拼板进 shot_runner；按镜片内音 | ✅ |
| 可灵 Omni 第 4b 波 | 样品 Phase A/cast 收窄；`shot_cast` partial；双桥装箱；generate 拼板 QC 再打一张 | ✅ |
| 可灵 Omni 第 5 波 | feature 选择卡；未选默认 regenerate；11–15s 回落；Skill / produce.md / EVOLUTION 可灵导演顺序例外 | ✅ |
| 可灵 Omni 第 6 波 | 403 不降 v1 单测锁；廉价路径 Image Omni / Omni 视频均 HTTP 400（非 404）；拼板+双桥因无余额推迟 | 闸 ✅ / 生成推迟 |
| 可灵真实联调 | 有余额后实测：`KLING_LIVE=1` 廉价档 3 passed（Image Omni + Omni 视频路径 400 非 404）；Image Omni 真生成 1 张 2k 壁纸成功（2720×1536，`assets/test_outputs/kling_wallpaper_cyber_tech.png`）；Bearer 鉴权 / v1 poll / 下载链路验证通过 | ✅ 图片面 / 视频面待测 |
| 可灵图片落地 1 | Image Omni 缺省 `kling-v3-omni`；显式 `kling-v1` 仍拒；PATH/POLL 未改 | ✅ |
| 可灵图片落地 2 | 人物拼板提示词先锁五格方位再填外形，全中文 ≤2500，无 `<<<image_` | ✅ |
| 可灵图片落地 3 | 道具四视提示词复用同一套格线（正/侧/背/四分之三），尚未接裁切工牌 | ✅ |
| 可灵图片落地 4 | 可灵环道具拼板裁切+QC+image_refer（不绑音色）；工牌名/描述截 20/100 | ✅ |
| 可灵图片落地 5 | 可灵首帧定格+视频方位中文加厚，按需吃满 ≤2500；视频仍 `@element`/`@image` | ✅ |
| 可灵图片落地 6 | 首帧 Image Omni 装箱 `element_list`；图侧 `<<<object_N>>>`/`<<<image_N>>>`；视频仍 `@` | ✅ |
| 可灵图片默认 | 静图默认 Kling Image 3.0 Omni（`kling-v3-omni`）；别名归一；乱填 MODEL 回默认；选型无锁时优先 kling | ✅ |
| 母带三段式 | 可灵环视频提示词三段式（母带块 + 契约块 + 逐镜块）：`lib/kling_master.py` 7 种母带模式、`_shot_contracts.py` 逐镜契约表（镜头类型/状态演进/弧线/卡司/人群/声画/能量曲线/四铁律）；playbook `master_pattern` 引用；能力边界 findings 上 `await_final_prompt` + `await_retry` 双卡 | ✅ |
| 母带层修复 | 契约 duration 统一为整片时间窗（`[Ns-Ms]` 时序锚准）；`ONE_TAKE_BEATS` 一镜到底走位进提示词；vertical_fall 补 6 镜类型表；契约块「时长」→「时段」；`cyberpunk_neon` 移除误引母带 | ✅ |

后续进化（导演八步 / Seedance·Kling·Agnes 主用面）以 [EVOLUTION_PLAN.md](EVOLUTION_PLAN.md) 为准；旧 V1/V2/V3 见 [archive/](archive/)。

## 测试

```bash
python scripts/minitest.py        # 需 pip install -e .（核心依赖 jsonschema）
```

## git 历史

```
1ac8697 P5: 文档与示例
8dafc7c P4: FastAPI + 单页 Web 看板
28a1946 P3: FFmpeg 合成引擎 + 配音装配
a8bf589 P2: 国产供应商适配器 + 选型器
4917eb2 P0+P1: 管线引擎/工具契约/注册表 + 中文词库与镜头提示词工程迁移
```

## 待办（用户侧）

- [ ] 配置真实密钥（AGNES/JIMENG/DASHSCOPE/DOUBAO）后联调"待联调"契约（Agnes 视频/配音）
- [ ] 自行执行一次真实成片：先放 clip，再 `python -m montage produce <项目目录>`（`examples/pipeline_flow.py` 仍是手调工具示例）
- [ ] （可选）把仓库移到独立目录、改为自己的 git 身份（当前为占位身份 montage-core@local）
- [x] 可灵图片面真实联调（Image Omni 2k 壁纸生成成功）；视频面（Omni 视频 / 双桥）待用户确认后测

## 契约分级说明

- **确定**：Agnes 图片、DashScope ASR、即梦图/视频、豆包 TTS、可灵图片（Image Omni 真实联调通过）
- **待联调（接口在、未用真实密钥核对）**：Agnes 视频/配音、可灵视频、混元、seed_audio、talking_head、lip_sync、music_gen
