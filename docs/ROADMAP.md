# ROADMAP — OpenMontage 能力全量迁移路线图

> 目标：把 OpenMontage 的功能能力（不是代码）尽可能完整地迁移到 montage-core，
> 自创实现、国内供应商优先、国外供应商留接口。本文件是迁移的指导文档与进度表。

## 0. 许可证边界（最重要）

- **OpenMontage 是 AGPL-3.0**（强 copyleft）：复制/翻译其代码会污染 montage-core
  的 MIT 许可，导致 GitHub 发布时被迫整体转 AGPL。
- **铁律：本迁移只借鉴"功能清单 / 行为语义 / 供应商接口契约"，实现代码 100% 自创**
  （montage-core 自己的风格：BaseTool 契约、零依赖、中文注释、确定性规则）。
- 供应商接口的"操作名/参数语义"对齐属接口契约（不受版权保护），但请求构造、
  签名、响应解析全部自写。
- 第三方供应商（火山引擎/阿里云/智谱等）的 HTTP 契约以官方文档为准，
  与 OpenMontage 无关。

## 1. 差距分析（OpenMontage 功能 → montage-core 状态）

### 已具备（montage-core ✅）

| 能力 | montage-core 实现 |
|------|-------------------|
| 词库检索 / 视觉提示词 / 剪辑顾问 | prompt_library_retriever / visual_prompt_builder / edit_advisor |
| 剧本质量门禁 / 人物卡 / 可拍性 | script_validator + DIRECTOR_GUIDE |
| 供应商选型器（图/视频/TTS） | image_selector / video_selector / tts_selector |
| 国产供应商：即梦图/视频、Agnes 图/视频/配音、豆包 TTS、DashScope ASR、Edge TTS | providers/ |
| 合成：拼接/裁剪/字幕/LUT/混音/转场/档案/配音装配/Ken Burns | compose/ |
| 质量门禁 / A·B 选优 / 生成缓存 | asset_quality_gate / asset_picker / generation_cache |
| 字幕生成（SRT/ASS） | subtitle_builder |
| 契约治理（产物门禁/白名单/预算对账） | engine/gates.py + budget |
| 知识资产（词库/剧本库/playbook/LUT/资产索引） | prompt_library + playbooks + assets |

### 待迁移（按批次）

| 批次 | 功能 | OpenMontage 出处 | 自创实现方案 |
|------|------|------------------|--------------|
| **M1** | 参数归一化（拼接前统一分辨率/fps/编码） | video_stitch | ffmpeg scale/pad/fps 重编码 |
| **M1** | 音频 acrossfade（转场时声音同步交叉淡化） | video_stitch | acrossfade 滤镜链 |
| **M1** | 空间布局（分屏/竖排/画中画） | video_stitch spatial | xstack / overlay |
| **M1** | 混合图层（blend 模式叠加） | video_blend_layer | blend 滤镜（screen/overlay/multiply/add…） |
| **M1** | 静音剪切/跳切 | silence_cutter | silencedetect 解析 + trim/concat |
| **M1** | 智能重构图（比例转换，人脸追踪可选） | auto_reframe | scale+crop；face 追踪 OpenCV 可选降级 |
| **M1** | 展示卡片（9:16 letterbox+标题） | showcase_card | drawtext + letterbox |
| **M1** | 变速（加速/慢放） | video_trimmer | setpts + atempo |
| **M2** | 抽帧 / 场景检测 / 视频分析 / 音频探测 / 音频能量 | analysis/* | ffmpeg fps/scene/volumedetect/ebur128 |
| **M2** | 视频下载 / 转写 / 字幕获取 | video_downloader / transcriber | urllib + 国内 ASR 接口 |
| **M3** | DashScope 生图（通义万相）/ TTS | dashscope_image / dashscope_tts | 阿里云官方契约 |
| **M3** | 智谱 CogVideo（清影） | cogvideo_video | 智谱官方契约 |
| **M3** | 可灵图/视频（kling） | kling_official_image / kling_official_video | 快手官方契约 |
| **M3** | 万相 / 混元 视频 | wan_video / hunyuan_video | 阿里/腾讯官方契约 |
| **M3** | 豆包 Seed Audio / piper 离线 TTS | doubao_seed_audio / piper_tts | 官方/本地契约 |
| **M3** | 音乐生成（Suno 等）接口 | music_gen | 抽象接口 + 默认不可用提示 |
| **M4** | 超分 / 去背 / 人脸修复（可选依赖+降级） | upscale / bg_remove / face_restore | 可选依赖模式（同 edge_tts UNAVAILABLE） |
| **M5** | 数字人口播 / 口型同步 / 录屏 | talking_head / lip_sync / screen_recorder | 国内数字人接口 + ffmpeg gdigrab |
| **M6** | 素材源检索（国内优先） | stock_sources / clip_search | pixabay/pexels API + 免费源 |
| **M6** | 导出包 | export_bundle | 清单+归档脚本 |
| **M7** | 更多管线配置 | pipeline_defs/ | Python dict 扩展（零 YAML） |

### 明确不迁移（记录原因）

| 功能 | 原因 |
|------|------|
| Remotion / HyperFrames 渲染运行时 | Node 生态会拆仓库；抄 OpenMontage 合成器会触 AGPL；本产品是拼接 AI 片段，FFmpeg 已覆盖 |
| 20+ 国外视频供应商（sora/veo/runway/heygen/gemini/grok/ltx/minimax…） | 用户主用国内；仅保留抽象 selector 接口，可后续按需补 |
| Microsoft Azure STT / Google 系 / OpenAI 系 | 同上，国外 |
| SVG 角色绑定动画（character_animation 全量） | 依赖 HyperFrames；保留简化版动作时间线思路 |

## 2. 实施原则

1. **自创实现**：每个功能独立设计，参考 ffmpeg/供应商官方文档，不参照 OpenMontage 源码。
2. **国内优先**：供应商先做国内可用（即梦/万相/可灵/混元/清影/豆包/DashScope/Edge），
   国外供应商统一抽象为接口 + `NEEDS_CONFIG`/`UNAVAILABLE` 状态，不阻塞。
3. **可选依赖降级**：OpenCV/rembg/超分等安装后可用、缺失则 UNAVAILABLE。
   核心 pip 依赖仅 `jsonschema`（产物门禁）；HTTP 走标准库 urllib。
4. **契约一致**：新工具进 `montage/tools/` 或 `montage/providers/`，注册表自动发现；
   新合成能力进 `montage/compose/`，`FFmpegCompose` 统一分发。
5. **每批可验证**：每批补测试，minitest 全绿，AGENT_GUIDE/README/PROGRESS 同步。

## 3. 进度

| 批次 | 状态 |
|------|------|
| M1 剪辑特效增强 | ✅ 完成（effects.py：归一化/空间布局/混合图层/变速/展示卡片/静音剪切/重构图 + 转场音频 acrossfade） |
| M2 分析工具 | ✅ 完成（frame_sampler/scene_detect/video_analyzer/audio_probe/audio_energy/downloader） |
| M3 国内供应商 | 万相图/视频、智谱清影 **确定**；可灵/混元/seed_audio/music_gen **接口在、未联调**；piper 离线 TTS 可用 |
| M4 增强接口 | ✅ 完成（upscaler 超分 / bg_remover 去背 / face_restorer 人脸修复；可选依赖+降级模式） |
| M5 数字人/录屏 | screen_recorder 可用；talking_head/lip_sync **接口在、未联调** |
| M6 素材源/导出 | ✅ 完成（stock_retriever 素材检索：wikimedia 免费无 key / pixabay / pexels；export_bundle 导出包） |
| M7 管线扩展 | ✅ 完成（documentary / clip_factory 两条新管线 + ROADMAP 终稿） |

## S 系列（生产流水线能力层，M1–M7 之后）

与 OpenMontage 工具迁移分开：补的是字段级自动化与确定性转换，不是再抄一套合成器。

| 批次 | 功能 | 状态 |
|------|------|------|
| **S0 / P0** | 剧本/镜头契约 additive（environment / lines[] / visual_details）；completeness 默认 warning；playbook 自动注入 style_context | ✅ |
| **S1 / P1** | 3 本新 playbook（口播 id=`spoken_explain`）+ 7 本 `script_style` + `style_matcher`；有 playbook 才把 completeness 升 critical | ✅ |
| **S2 / P2** | `script_to_scene_plan`：1 section = 1 scene + N nested shots；overwrite 保护；不提前写 `shot_prompts` | ✅ |
| **S3 / P3** | `reference_assets` + 能力表 + `shot_runner`（默认 dry_run） | ✅ |
| **S4 / P4** | `voice_director` + 音色表 + `soundtrack_planner` + `place_audio`；assemble 默认 ducking/loudnorm | ✅ |
| **S5 / P5** | `compose_plan` 编译为 `edit_decisions`（assemble 仍只读后者）；`compose_plan` **不进** compose produces；`render_kind` 契约预留 | ✅ |
| **S6 / P6** | 图形镜覆盖层（HyperFrames 可选）；本轮不做 | 后置 |

P5 用法：`compose_planner` → 可选 `place_audio` → `ffmpeg_compose assemble`。图形 `render_kind` 本轮降级为 `ai_clip` 并记 finding。

## 4. 迁移总结

- **工具数**：20 → **53**（含 ffmpeg_compose 已纳入 discover；国内供应商、剪辑/分析/增强/录屏/导出/素材检索、S 系列转换器）。
- **管线数**：1 → **3**（cinematic / documentary / clip_factory）。
- **测试**：193 → **403**（minitest 33 文件；核心依赖 jsonschema）。
- **许可证**：全部代码为 montage-core 自创（MIT）；未参照 OpenMontage（AGPL）源码，
  仅借鉴功能清单与官方 API 契约。
- **待用户侧联调**：可灵/混元/seed_audio/talking_head/lip_sync（需真实密钥核对契约）；
  万相/智谱清影契约结构就绪，配 DASHSCOPE_API_KEY / ZHIPU_API_KEY 即可用。

## 5. 后续可选增强（不在本次迁移范围）

- 国外视频供应商（sora/veo/runway 等）：selector 接口已预留，按需补适配器。
- Remotion/HyperFrames 渲染运行时：Node 生态 + AGPL 风险，不迁移；FFmpeg 为唯一 compose 引擎。
- 逐词动画字幕（remotion_caption_burn）：需 Remotion，可用 subtitle_builder(ASS) 近似替代。
- 角色 SVG 绑定动画：依赖 HyperFrames，暂不迁移。
