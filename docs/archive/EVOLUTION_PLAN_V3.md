> **已被取代。** 现行方案见 [docs/EVOLUTION_PLAN.md](../EVOLUTION_PLAN.md)。本文仅作历史归档。

# montage-core 进化方案（完整版 · V3.2 · 主用模型锁定）

> 唯一权威的完整升级方案，取代 V1/V2/V3/V3.1。目标：**一句话创意 → 电影级成片，人工干预
> 降到 1~2 次关键确认 + 异常兜底**。
>
> **本轮范围**：视频生成只做 **即梦 / 可灵 / Agnes** 三家；其余供应商接口保留、走通用兜底。
>
> **主用模型（用户拍板，D7）**：
> - **即梦**：以最新两款为主 —— **Seedance 2.5**（旗舰，30s）+ **Seedance 2.0 Pro**（高质量）。
> - **可灵**：以最新两款为主 —— **Kling 3.0 Omni**（15s 多镜头+原生音频）+ **Kling 2.1 Pro**（图生视频旗舰）。
> - **Agnes**：**继续用 2.0**；当 2.5 可稳定使用时**弃用 2.0**、切换到 2.5。
>
> 三项原则：① VLM 质检用千问；② 公共能力合并、差异能力才分路；③ 提示词规范库前置。
>
> 许可证红线不变：MIT；代码与提示词 100% 自创；OpenMontage/ArcReel（AGPL）只借功能清单。

---

## 0. 决策记录

| # | 决策 | 结论 |
|---|------|------|
| D1 | VLM 质检 | **千问（DashScope Qwen-VL）**，工具化调用 |
| D2 | 视频供应商 | **只做即梦 / 可灵 / Agnes** |
| D3 | 路由原则 | **公共能力统一路径、差异能力才分叉** |
| D4 | 提示词 | **前置规范库**：进入路径前按 API 官方规范定好写法 |
| D5 | 人审保留点 | `human_checkpoints` 默认 `["proposal","script"]` |
| D6 | MVP | P0 能力表+Autopilot+提示词库 → P1 千问质检+场记 → P3 导演语言 |
| **D7** | **主用模型** | **即梦：Seedance 2.5 + 2.0 Pro；可灵：Kling 3.0 + 2.1 Pro；Agnes：2.0（2.5 可用即弃 2.0）** |

---

## 1. 目标定义（可验收）

### 1.1 "电影级"6 维度

| # | 维度 | 判据 |
|---|------|------|
| C1 | 角色/场景/道具跨镜头一致 | 同角色跨 ≥5 镜不漂移 |
| C2 | 镜头语言/运镜贴合情绪 | 运镜可解释，构图无遮挡/出画 |
| C3 | 光影/色调统一 | 跨镜头色调一致，整片一次调色 |
| C4 | 音画/对白/口型 | 对白镜不是"旁白声+无口型" |
| C5 | 节奏/转场/剪辑 | 转场与情绪匹配 |
| C6 | 无硬伤 | 坏帧/崩坏/漂移被质检拦截并重生成 |

### 1.2 "少人工"度量

5 门禁降到"提案+样片两处确认，其余机器收口（`approved_by=produce`，永不伪造
`human_approved`），机器判不过才升级人工并带 `proposed_fix`"。

---

## 2. 主用模型现状对齐（核心）

> 已核对仓库代码（`jimeng.py`/`kling.py`/`agnes.py`）与三家官方最新文档。
> 标注：✅=已实现且确定；🆕=官方最新但仓库未接；🔧=待联调核对。

### 2.1 即梦（火山引擎 Seedance）—— 主用 Seedance 2.5 + 2.0 Pro

| 项 | 现状 |
|----|------|
| 仓库已实现 | `jimeng_t2i_v40`（图）+ `jimeng_ti2v_v30_pro` / `jimeng_t2v_v30(_1080p)` / `jimeng_i2v_first_v30(_1080)` / `jimeng_i2v_first_tail_v30(_1080)` / `jimeng_i2v_recamera_v30`（视频）✅（**v30 家族**） |
| **主用模型（D7）** | **Seedance 2.5**（旗舰，单次 30s）🆕 + **Seedance 2.0 Pro**（高质量）🆕 |
| 首帧 i2v | ✅ |
| 尾帧桥（首尾帧约束） | ✅（v30 `i2v_first_tail`；Seedance 2.0/2.5 待核对） |
| 运镜模板 | ✅ `jimeng_i2v_recamera_v30` + `template_id` |
| 多镜头叙事 | ✅ Seedance 2.0+ 🆕 |
| 音频同步 | ✅ Seedance 2.0+ 🆕 |
| 参考图 | ✅ 文生图 `image_urls` ≤10 |
| 负向提示词 | ✅ |
| 时长 | v30 5/10s；**Seedance 2.5 单次 30s** 🆕 |
| 提示词规范 | 中文叙事句，`maxLength 800`，`seed` 可复现 |

**结论**：即梦需**从 v30 家族升级到 Seedance 2.0 Pro + 2.5**，解锁多镜头叙事 + 音频同步 +
30s 长镜头。v30 家族保留作为降级兜底（首尾帧桥/recamera 已在）。

### 2.2 可灵（快手 Kling）—— 主用 Kling 3.0 + 2.1 Pro

| 项 | 现状 |
|----|------|
| 仓库已实现 | `kling-v1`（文生图 + 文生/图生视频，5/10s）🔧（contract=pending-verify） |
| **主用模型（D7）** | **Kling 3.0 Omni**（15s 多镜头故事板 + 原生音频）🆕 + **Kling 2.1 Pro**（图生视频旗舰）🆕 |
| 首帧 i2v | ✅ |
| 尾帧桥 | ❌（官方无首尾帧约束） |
| 一场多镜（多镜头故事板） | ✅ Kling 3.0 🆕 |
| 原生音频 | ✅ Kling 3.0 🆕 |
| 多图参考 | ✅ Kling 3.0（multi-image Omni）🆕 |
| 运镜 / 运动控制 | ✅ motion control 🆕 |
| 时长 | Kling 2.1 Pro 3~15s；Kling 3.0 15s |
| 提示词规范 | 中文叙事句 🔧 待联调确认长度/负向 |

**结论**：可灵是三家**仓库实现最落后**（停在 v1），但**官方天花板最高**。必须从 v1 升级到
**Kling 2.1 Pro + 3.0** 并真实联调，才能解锁"一场多镜 + 原生音频"。

### 2.3 Agnes —— 主用 2.0，2.5 可用时弃 2.0

| 项 | 现状 |
|----|------|
| 仓库已实现 | 图：`agnes-image-2.1-flash`/`agnes-image-2.0-flash`；视频：`agnes-video-v2.0` + `agnes-video-2.5` ✅ |
| **主用模型（D7）** | **agnes-video-v2.0**（当前主用）；2.5 作为后续替换，稳定后弃 2.0 |
| 关键帧链 | ✅ 2.0 `mode=keyframes` |
| 首帧 / 尾帧 | ✅ 首帧 `image`，尾帧拼入关键帧链 |
| 片内音（无独立 TTS） | ✅ 音频随视频提示词生成（`AgnesAudio` UNAVAILABLE） |
| 负向提示词 | 2.0 ✅（默认英文负向）；2.5 ❌ 禁止 |
| 时长 | 2.0 3/5/10/18s（`num_frames` 8n+1）；2.5 4~12s |
| 提示词规范 | 英文，`maxLength 3000`；参考图必公网 http(s) URL；RPM 1 |

**结论**：Agnes **2.0 已到位且确定**，本轮以 2.0 为基准；2.5 的切换策略见 §8（风险/切换）。

### 2.4 三家能力矩阵（真实字段，供路由消费）

| 能力 | 即梦（Seedance 2.0 Pro/2.5） | 可灵（2.1 Pro/3.0） | Agnes（2.0 主用） |
|------|------------------------------|---------------------|-------------------|
| 首帧 i2v | ✅ | ✅ | ✅ |
| 参考图（定妆） | ✅ ≤10 | ✅ 多图参考 | ✅ `reference` |
| 负向提示词 | ✅ | 🔧 待确认 | ✅（2.0） |
| 尾帧桥 | ✅ | ❌ | ✅ 关键帧首尾 |
| 运镜（原生） | ✅ recamera / 2.0 多镜头 | ✅ motion control | ❌ |
| 一场多镜 | ✅（Seedance 2.0+） | ✅（3.0） | ❌ |
| 原生音频/片内音 | ✅（Seedance 2.0+） | ✅（3.0） | ✅ 片内音 |
| 关键帧链 | ❌ | ❌ | ✅ |
| 时长 | 5/10s（v30）/ 30s（2.5） | 3~15s（2.1）/ 15s（3.0） | 3/5/10/18s（2.0） |
| 提示词语言 | 中文 | 中文 | 英文 |

---

## 3. 能力路由：公共能力合并 + 差异能力分叉

### 3.1 公共能力（三家共用同一条路径）

| 公共路径 | 统一实现 | 能力开关 | 不支持时降级 |
|----------|----------|----------|--------------|
| 首帧 i2v | `apply_video_frames` 统一注入首帧 | `first_frame` | 纯文生 t2v |
| 参考图定妆 | `apply_image_refs` 统一注入定妆照 | `image_reference` | 纯文生，外观锚点文本兜底 |
| 负向提示词 | `prompt_adapter` 统一注入负向 | `negative_prompt` | 忽略负向 |
| 时长贴网格 | `snap_duration_seconds(duration_policy)` | `duration_policy` | 均分/不贴 |
| 分辨率比例 | `aspect_ratio` 归一化 | — | — |

### 3.2 差异能力（才分叉）

| 差异能力 | 即梦 | 可灵 | Agnes | 分叉处理 |
|----------|------|------|-------|----------|
| 尾帧桥 | ✅ | ❌ | ✅ 关键帧首尾 | 有则桥，无则只首帧 |
| 原生运镜 | ✅ recamera / 2.0 多镜头 | ✅ motion control | ❌ | 有则原生参数，无则提示词暗示 |
| 一场多镜 | ✅ Seedance 2.0+ | ✅ 3.0 | ❌ | 即梦/可灵可走，`gen_strategy` 开关 |
| 原生音频/片内音 | ✅ Seedance 2.0+ | ✅ 3.0 | ✅ 片内音 | 有则片内音，无则 TTS+ducking |
| 关键帧链 | ❌ | ❌ | ✅ | 仅 Agnes，`keyframe_chain` |

### 3.3 路由决策流程（`shot_runner._route_shot`）

```
读 supplier = selector(video_loop)
读 caps = capabilities.video_caps(tool=supplier)
读 prompt_spec = video_prompts.PROFILES[supplier]   # 提示词规范前置

1) 提示词（前置）：StandardVisualPrompt → prompt_adapter 方言化 → 校验 → final_prompt
2) 公共路径：首帧？参考图？负向？时长？→ 统一注入（能力表开关）
3) 差异分叉：尾帧桥 / 原生运镜 / 一场多镜 / 原生音频 / 关键帧链 → 按 3.2 表
4) 输出 {path, payload, degraded, notes[]}，每条降级写 notes 进决策日志
```

---

## 4. 提示词规范库（前置）

### 4.1 平台无关中间格式 `StandardVisualPrompt`

| 字段 | 说明 | 必填 |
|------|------|------|
| `subject` | `characters[]`（id/appearance/outfit/action/emotion）+ `props[]` | ✅ |
| `environment` | location/lighting/color_tone/atmosphere/era | ✅ |
| `camera` | shot_size/camera_movement/angle/lens_mm | ✅ |
| `dialogue` | 对白（原生音频/片内音消费） | 条件 |
| `negative` | 负向（即梦 / Agnes 2.0 消费） | 条件 |
| `duration_seconds` | 时长（贴各家 `duration_policy`） | ✅ |

### 4.2 三家官方提示词规范档案 `VIDEO_PROMPT_PROFILES`

`montage/providers/video_prompts.py`（Python dict，零依赖）。**按主用模型写死**：

```python
VIDEO_PROMPT_PROFILES = {
  # 即梦 Seedance 2.0 Pro / 2.5：中文叙事句；运镜走 recamera template_id 或 2.0 多镜头
  "jimeng_video": {
    "models": ["seedance-2.5", "seedance-2.0-pro"],  # 主用（v30 家族作降级兜底）
    "language": "zh", "style": "中文叙事句", "max_chars": 800,
    "required": ["subject", "environment"],
    "negative": True, "seed": True,
    "camera": "template_id",      # recamera 运镜模板
    "multi_shot": True,           # Seedance 2.0+
    "native_audio": True,         # Seedance 2.0+ 音频同步
    "duration_grid": [5, 10, 30], # 2.5 支持 30s
    "forbidden": ["分镜脚本式", "时间戳", "超过 800 字"],
  },
  # 可灵 Kling 2.1 Pro / 3.0：中文叙事句；3.0 一场多镜 + 原生音频
  "kling_video": {
    "models": ["kling-3.0", "kling-2.1-pro"],  # 主用
    "language": "zh", "style": "中文叙事句", "max_chars": 500,  # 待联调确认
    "required": ["subject", "environment", "camera"],
    "negative": False, "seed": False,
    "multi_shot": True,           # 3.0
    "native_audio": True,         # 3.0
    "duration_grid": [5, 10, 15], # 2.1 Pro 3~15s / 3.0 15s
    "forbidden": ["负向描述"],
  },
  # Agnes 2.0（主用）：英文关键帧链；2.5 可用后切换并弃 2.0
  "agnes_video": {
    "models": ["agnes-video-v2.0"],   # 主用；2.5 稳定后替换
    "language": "en", "style": "关键帧链 + 片内台词", "max_chars": 3000,
    "required": ["subject", "environment", "keyframe_url"],
    "negative": True,             # 2.0 支持；切 2.5 后改 False
    "seed": False,
    "keyframe_chain": True, "native_audio": True,
    "duration_grid": [3, 5, 10, 18],
    "forbidden": ["TTS 旁白", "叠 BGM", "本地图片路径（必须公网 URL）"],
  },
}
```

### 4.3 知识页 + LLM 使用协议（前置五步）

`prompt_library/video_gen/` 建 3 页：`INDEX.md` + `jimeng.md` + `kling.md` + `agnes.md`。
`INDEX.md` 顶部五步协议：

1. 读 `capabilities.video_caps(tool=供应商)` → 知道支持什么；
2. 读 `VIDEO_PROMPT_PROFILES[供应商]` → 知道语言/格式/长度/字段/网格/禁忌；
3. `visual_prompt_builder` 产出 `StandardVisualPrompt`；
4. `prompt_adapter` 按 `template` 方言化；
5. `prompt_adapter` 校验（长度/必填/网格/禁忌）→ 不合格就拆镜或降级，绝不硬发。

### 4.4 一个镜头 → 三家三种方言（示例）

同一镜头"主角黑风衣雨中转身，紧张，中景缓缓推近"：

- **即梦 Seedance**（中文叙事句 + 负向 + seed）：
  `【主体】穿黑色风衣的男子雨中转身，神情紧张，【场景】雨夜霓虹街道冷色调，【运镜】中景缓缓推近，负向：画面崩坏、多余手指`
- **可灵 Kling 3.0**（中文叙事句，一场多镜 + 原生音频）：
  `一场：黑风衣男子雨中转身，神情紧张，中景缓缓推近，雨夜霓虹冷色调；对白："……"（原生音频）`
- **Agnes 2.0**（英文关键帧链 + 片内音）：
  `keyframe: <公网URL>; a man in black trench coat turns in the rain, tense, medium shot slow push-in, rainy neon street cold tone; in-scene audio: heavy breathing`

---

## 5. VLM 质量审查（千问，已确认）

- 新增 `montage/tools/vlm_reviewer.py`（`capability="analysis"`），provider 固定 DashScope Qwen-VL，
  复用 `DASHSCOPE_API_KEY`，接口按阿里云官方文档自写。
- 输入 `{media_path, mode, shot, character_registry, script, expected}`；输出
  `{ok, score, issues[]}`（`severity/kind/message/proposed_fix`）。
- 工具化调用；未配密钥 → `NEEDS_CONFIG`，回退 `asset_quality_gate`。

---

## 6. 分阶段实施计划

### P0 — 少人工骨架 + 能力表 + 提示词库地基（第一优先）

1. `capabilities.py`：补 `multi_shot/native_audio/lipsync/camera_control/multi_subject_refs/
   max_duration/negative_prompt/turnaround`；**按 §2.4 填三家主用模型真实值**。
2. `schemas.py:PROPOSAL_PACKET_SCHEMA` 补 `autopilot/human_checkpoints/dialogue_audio_mode/
   gen_strategy/race_providers/race_budget_factor`。
3. 新增 `montage/engine/autopilot.py`（`review_stage`/`auto_approve`）。
4. `idea_developer.py` 加 `operation=autowrite`。
5. `produce.py`：`GEN_STEP_IDS` 前插 `autopilot`；`--autopilot` 参数。
6. **提示词库地基**：`video_prompts.py`（三家主用模型档案）+ `prompt_library/video_gen/`（INDEX+3 页）。

**验收**：`--autopilot full` 确定性全绿零人工跑通；`doctor --json` 列出三家能力字段；
`prompt_adapter` 把同一 `StandardVisualPrompt` 渲染成三家三种方言。

### P1 — 电影级一致性（千问质检 + 场记 + 四视图）

1. `schemas.py:SCRIPT_SCHEMA.characters` 加 `turnarounds`；`reference_assets.kind` 加 `turnaround`；
   `shot_runner` 生成多视图，`apply_image_refs` 支持多 URL。
2. 新增 `montage/engine/continuity.py` + `artifacts/continuity.json`；`shot_runner` 注入场记摘要；
   `episodes.py` 跨集读。
3. 新增 `vlm_reviewer.py`，接进 `shot_runner` 质检钩子；`_ASSETS_CORE` 加 `"vlm_reviewer"`；
   `gates.py` 扩展"确定性+VLM 均挡 critical"。

**验收**：跨 ≥5 镜千问揪出"黑发变金发"并重生成；未配密钥降级 warning。

### P2 — 能力路由 + 模型升级联调 + 提示词库完整

1. `shot_runner._route_shot(caps, shot, ...)` 实现 §3.3 流程（公共合并 + 差异分叉）。
2. `prompt_adapter.py` 完整实现 StandardVisualPrompt → 方言渲染 + 校验。
3. **即梦升级**：`jimeng.py` 从 v30 家族升级到 **Seedance 2.0 Pro + 2.5**（多镜头 + 音频同步 +
   30s）；v30 家族保留作首尾帧桥/recamera 兜底。
4. **可灵升级**：`kling.py` 从 `kling-v1` 升级到 **Kling 2.1 Pro + 3.0**；联调后更新
   `multi_shot=True`/`native_audio=True`。
5. 分段重拍：`effects.py` 加 `retake_segment`（`-c copy`）+ `rework_requests.json` sidecar。
6. 可选竞赛：`selectors.py` 加 `race` 模式（`asset_picker` + 千问双评分）。

**验收**：同一镜头——即梦 Seedance 走"多镜头+音频同步+负向"，可灵 3.0 走"一场多镜+原生音频"，
Agnes 2.0 走"关键帧链+片内音"，提示词各符合各方言；坏段重拍不重编码好段。

### P3 — 导演语言（节拍→运镜 + 构图审计 + sketch-lock）

1. 新增 `montage/director.py`：`beat_to_camera`（hook→push_in …）。
2. `script_validator.py` 加 `purpose=composition` / `purpose=beat_coverage`。
3. `shot_runner` 加 `sketch_lock`。
4. 运镜走法：即梦→recamera `template_id` / 2.0 多镜头；可灵→motion control；Agnes→提示词暗示。

**验收**：无运镜镜头自动获得节拍驱动运镜；构图审计生成前报遮挡/出画。

### P4 — 音画后期（对白策略 + 原生音频 + 健康门禁）

1. `dialogue_audio_mode(native|tts|lipsync)` 落地：即梦 Seedance 2.0+/可灵 3.0/Agnes 2.0 对白镜
   走原生音频/片内音；无原生音频时走 TTS+`lip_sync`（补齐联调）。
2. 新增 `montage/tools/film_health.py`：发布前 ffprobe 体检，critical 挡 `export_bundle`。
3. `effects.py` 加 `grain` + `cinematic_21_9`。

**验收**：对白镜不再"旁白声+无口型"；发布前自动全片健康报告。

### P5 — 交付（WebUI + 剪映草稿 + 模板）

1. WebUI：blocking 俯视图 + 逐帧审查（`frame_sampler`+`vlm_reviewer`）+ 勾选返工。
2. `export_bundle` 加剪映草稿（自创实现）。
3. 模板市场：成功项目 → 模板复用。

**验收**：WebUI 完成"确认提案→审查分镜→勾选返工→出片"。

---

## 7. 优先级 + MVP

| 阶段 | 影响 | 成本 | 风险 | 顺序 |
|------|------|------|------|------|
| P0 能力表+Autopilot+提示词库地基 | 高 | 中 | 低 | **第一** |
| P1 千问质检+场记+四视图 | 最高 | 高 | 中 | **第二** |
| P2 能力路由+即梦/可灵升级+提示词库完整 | 高 | 高 | 中 | 第三 |
| P3 导演语言 | 中高 | 低 | 低 | 可与 P0 并行 |
| P4 音画后期 | 中 | 中 | 中 | 第四 |
| P5 交付 | 中 | 中高 | 低 | 最后 |

**MVP（先做三件）**：① P0 能力表+Autopilot+`video_prompts.py`；② P1 千问质检+场记；
③ P3 节拍→运镜+构图审计。

---

## 8. 风险、切换与降级

- **千问不可用**：`vlm_reviewer` `NEEDS_CONFIG`，回退 `asset_quality_gate`。
- **即梦 Seedance 2.0/2.5 联调失败**：回退 v30 家族（首尾帧桥 + recamera 已在），
  多镜头/音频同步/30s 推迟，不影响出片。
- **可灵 2.1 Pro/3.0 联调失败**：回退逐镜 + TTS 后叠，一场多镜/原生音频推迟。
- **Agnes 2.5 切换**：以 2.0 为基准联调稳定后，把 `agnes_video` 默认模型切到 2.5，
  同步更新能力表（`negative_prompt` → False、时长 4~12s、多模态占位符），**再弃 2.0**；
  切换期间 2.0/2.5 双跑对照，未稳定不弃 2.0。
- **能力字段缺失**：缺省 False/空值；每条降级写 `notes`。
- **提示词超长/漏字段/踩禁忌**：`prompt_adapter` 确定性校验拦截，拆镜或降级。
- **机器收口误判**：只对"确定性全绿 + 千问无 critical"收口，写 `decision_log`。
- **成本失控**：`budget_ceiling_usd` 硬停 + dry_run 估算；race 用 `race_budget_factor`。
- **许可证**：代码与提示词 100% 自创。

---

## 9. 一句话总结

- **主用模型锁定**：即梦 = Seedance 2.5 + 2.0 Pro；可灵 = Kling 3.0 + 2.1 Pro；Agnes = 2.0（2.5 可用即弃 2.0）。
- **公共能力合并**：首帧/参考图/负向/时长/比例五条统一路径；尾帧桥/原生运镜/一场多镜/
  原生音频/关键帧链五条差异分叉。
- **提示词规范前置**：进入路径前按三家主用模型官方规范定好写法。
- **升级重点**：即梦 v30→Seedance 2.0/2.5、可灵 v1→2.1 Pro/3.0，是解锁"多镜头+原生音频"的关键。
- **P0 少人工、P1 电影级、P2 路由+升级、P3 导演、P4 音画、P5 交付**。

现有资产（预算账本、确定性门禁、镜头分层成本、多供应商显式路由、可审计、170 条中文词库 +
7 playbook）是开源同类最扎实的一档；本方案在其上做增量，不推翻、不破坏 MIT 边界。
