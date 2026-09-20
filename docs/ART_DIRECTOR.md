# ART_DIRECTOR — 美术指导执法手册

> 美术指导是协议角色（Agent 扮演），执法项与审读范围见 [ROLES.md](ROLES.md)。
> 确定性工具：schema 校验、`script_validator`、playbook `quality_rules`、VLM 一致性。

## 审读范围

- `await_outline`（**美术帽轻审，P0-8 新增**）：对照 format_card（用户需求）与
  bible 的 tone/playbook/visual_language，早期对齐风格基调；findings 记
  `subject=outline`。同轮换帽，不新增停点。
- `await_cast`：定妆/四视图/空镜/道具图（风格、美感；像不像归 VLM）。
- `await_shots`（点位一）：subjects/objects/location_sensory/shot_language 对照
  playbook quality_rules、词库引用、跨镜一致性；**审各镜 `vfx[]` 风格**
  （特效指导制定、美术把关——特效观感是否符合 playbook 美学与用户需求，P0-8）。
- `await_frames`：首帧抽检——失败镜+VLM critical 镜+全部 hero 镜必读，其余每第 5 镜抽 1
  （确定性可复现，V20）。**抽检发现问题→重抽前先六步归因，不是自动放行（V26）。**
- `await_final_prompt`（点位二）：子 Agent 首审读真实提示词总览（仅一次，
  record 带 `phase=first_pass`）；之后主 Agent 换帽复审。
- `await_clips`（**风格终检，P0-8 新增**）：`vlm_reviewer mode=video_clip`
  抽整片帧对照 playbook `quality_rules`/`visual_language` + 审各镜 `vfx[]`
  美学一致性（与特效帽同轮换帽共用额度）。**成本护栏复用 P0-7a**：抽帧 ≤12、
  缺 `DASHSCOPE_API_KEY` 跳过降级；结论走 review_log+停点呈报，不改 critical 语义。

## 执法项

1. **细粒度元素（①）**：屏幕文字/界面/小图案→critical（AI 生成必然崩坏），
   除非用户至上条款豁免。
2. **身份/动作分离（⑦）**：有参考图绑定的镜，`appearance_anchor` 短语级，
   提示词只描述动作+服装变化+情绪；无参考图通道才全量身份描述。
   身份从简→字数降→`await_prompt` 超长停减少。
3. **定妆类重抽前置（V15）**：`--retry portrait/turnaround/scene_ref/prop` 前走六步归因
   （见 [ROLES.md](ROLES.md) 第 6 节），写入重抽结论后再执行。

## 字段编辑纪律

- 镜头级视觉修复走 `bible.scenes[].shots[]` → **先重编译再 --resume**
  （`python -m montage run <dir> idea_developer --input inputs.json`，inputs 仅
  `{"operation":"compile"}`；前置检查 status 为 await_*）。
- **action 四字段（verb/manner/body_part/contact）整组写**——只写 verb 会残留
  plan 侧"说话/站立"stub 的 manner（V27）。
- 每轮 REVISE 必须 `review_logger record`（role=art_director，subject 按枚举表，
  返修轮 phase=revise）；轮次 ≤4（点位一+点位二共用）。
- 换供应商出口：换后瞄一眼方言重建是否保留关键元素（V14，一行即可）。
