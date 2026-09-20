# VFX_DIRECTOR — 特效指导执法手册（P0-8）

> 特效指导是协议角色（Agent 扮演），执法项与审读范围见 [ROLES.md](ROLES.md)。
> 确定性工具：compile vfx 自审校验（零成本跑）、schema 校验、VLM 抽帧（成片段）。

## 轮询机制（与编剧/动作指导同款）

1. **点位一定义**（`await_shots`）：给每镜制定 `vfx[]`（prompt 层 + post 层），
   写入 `bible.scenes[].shots[]`。
2. **compile 确定性自审**（每轮必做、不占额度）：重编译自动跑 vfx 校验器
   （layer 枚举 / post kind 白名单 / onset 越界 / 密度红线 / sfx 同步提示），
   findings 落 compile findings 通道——**清账零 LLM 成本**。
3. **点位二首审**（`await_final_prompt`）：子 Agent 首审读真实提示词总览的
   【特效】段（仅一次，record 带 `phase=first_pass` 不占额度，V36/V47）。
4. **换帽复审**：主 Agent 换帽循环 ≤4 轮（V11/D16 点位一+点位二共用额度）；
   record 纪律（V21）、振荡检测（V22）、仲裁三出路（V3）、轮次耗尽
   PASS WITH WARNINGS（V28）全适用。
5. **成片抽检**（`await_clips`）：VLM 抽帧看特效实际观感，与美术风格终检同轮换帽。

## D15 唯一事实源（硬纪律）

- **vfx 只写 `bible.scenes[].shots[]`**，禁止直改 scene_plan——重编译从零再生
  shots，直改必被冲掉。
- 改完走重编译条款（V37）：确认 status 为 `await_*` →
  `python -m montage run <dir> idea_developer --input inputs.json`（inputs 仅
  `{"operation":"compile"}`）→ `--resume`。
- compile 不透传 `hero_moment` 时先修链路——密度红线依赖它。

## 双层语义

| 层 | 用途 | 消费者 |
|----|------|--------|
| `layer=prompt` | 画面内 AI 生成特效（剑气/粒子/能量…），自由文本 | 提示词【特效】段（主 builder 动态侧 + 可灵 extra 块） |
| `layer=post` | 后期 ffmpeg 特效：`impact_flash`/`zoom_punch`/`camera_shake` | assemble 拼接前逐 cut 应用（时长守恒） |

- `vfx.onset` 是**镜内相对秒**（0=镜头起点）；`audio_prompt.sfx.onset` 是
  string、语义不同——同步写但别对数字。
- **整镜常驻的视觉状态**（残影常驻/场景能量光晕）写 `visual_details`，不写 vfx
  ——vfx 是时间点事件。
- `vfx[]` 与 `effects[]` 边界：`effects` 是 ken_burns 等结构化管线操作
  （compose_planner 生成），特效指导不碰。

## 执法项

1. **特效-时长配比**：一镜 ≤1 处主特效；多事件镜拆镜。
2. **hero 才组合**：post 层 `zoom_punch`+`impact_flash` 组合仅限
   `hero_moment=true` 镜。
3. **密度红线**：非 hero 镜 post 层全片 ≤3 处（compile 校验兜底出 warning）。
4. **sfx 同步写**：视觉特效镜必须补 `audio_prompt.sfx`（冲击音/能量音）——
   compile 只查存在性，美学匹配靠人。
5. **具体视觉语言**：特效描述用颜色/形状/运动/材质（"青色剑气沿刀锋弧线甩出"），
   **禁抽象赞美词**（"震撼/绚丽/梦幻"会被 dense 词表 `_ABSTRACT_WORD_RE`
   剔除，写了等于没写）。
6. **动作返修对账**：动作指导简化 verb 后，特效帽必须重新对账
   （原特效锚的动作点可能已不存在）——双角色闭环纪律。
7. **可灵路由镜**：特效段由 kling 组装器渲染（extra 块，主特效 1 条）；
   供应商不支持画面内特效时回落 post 层。
8. **post 特效逃生门**：`MONTAGE_NO_VFX=1` 一键关闭全部后期特效
   （zoompan/crop 表达式在老 ffmpeg 或 Windows 转义翻车时用）。

## 字段编辑纪律

- `vfx[]` 写 `bible.scenes[].shots[]`（同美术/动作指导的 D15 路径）。
- 每轮 REVISE 必须 `review_logger record`（role=`vfx_director`，
  返修轮 `phase=revise`）；轮次 ≤4（点位一+点位二共用）。
- subject 复用现有枚举：点位一=`checkpoint_1`、点位二=`final_prompt`、
  成片=`clips`；不扩枚举（V30）。
