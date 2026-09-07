# OpenMontage Prompt Library (提示词库)

面向中文 AI 视频/图片生成的**结构化提示词库**，作为画面/特效/动作/镜头/光线/风格的参考事实来源。

## 使用方式

在 `scene-director` / `asset-director` 的逐镜头分析阶段，调用
`prompt_library_retriever` 工具按「场景类型 + 风格 + 情绪 + 动作密度 + 镜头」检索
top-K 相似词条，作为 LLM 改编出 `visual_details` / `cinematography` / `action_sequence`
等结构化字段的**参考上下文**。词条只作参考，LLM 需"参考→改编→只取关键要素"，禁止整段照抄。

## 词条字段

每个词条是一个 YAML 文件，字段约定：

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | str | 唯一标识，形如 `style/ghibli` |
| `category` | str | 分类（scenes/effects/actions/shots/lighting/styles） |
| `title` | str | 中文名称 |
| `tags` | list[str] | 检索关键词（场景类型/情绪/风格/动作等） |
| `emotion` | str | 主要情绪基调 |
| `action_density` | str | `low`/`medium`/`high` |
| `shot_kind` | str | `first_frame` / `video` / `both`（静态首帧 vs 动态视频适用性） |
| `prompt` | str | 可直接参考的提示词片段（中文，含量化动作/光线/镜头描述） |
| `notes` | str | 使用注意（何时用、避免什么、控制信号） |

## 词条来源与授权

- 词条内容为人工精选/改写自以下 **MIT License** 开源仓库（保留版权声明）：
  - `Wayhhow/ai-video-shot-prompt-skill`
  - `jijiutong/ai-visual-director`
  - `MapleShaw/seedance2.0-prompt-skill`
- 部分内容综合自 OpenMontage 自身技能：`skills/creative/video-gen-prompting.md`、
  `skills/pipelines/chinese/cinematic-language.md`、`skills/creative/provider-prompt-rules.md`。
- 只提取词条内容自建库，不整仓复制；保留出处见 `CREDITS.md`。

## 目录

- `scenes/` — 场景型（战斗/追车/雨夜/山水/废墟…）
- `effects/` — 特效型（爆炸/粒子/流体/能量/镜头光晕…）
- `actions/` — 动作型（动作节拍量化模板/姿态/表情）
- `shots/` — 景别/运镜/角度/焦段/转场原语
- `lighting/` — 光线/色调/风格化影调
- `styles/` — 风格预置（中国风/吉卜力/赛博朋克/水墨…）
- `directors/` — 导演风格/类型范式
- `scripts/` — 剧本/对白写作范式（含三幕/英雄之旅/人物弧光结构模板）
- `screenplays/` — 公有领域剧本范式（老舍《茶馆》/关汉卿《窦娥冤》/莎士比亚《哈姆雷特》等 12 条）
- `edits/` — 剪辑/转场/节奏决策范式
- `INDEX.md` — 全部词条索引
- `video_gen/` — 视频 API 面知识页（INDEX 五步 + seedance/kling/agnes；不计入词条数）
- `CREDITS.md` — 来源与授权说明
