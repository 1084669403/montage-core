# Edits — 剪辑/转场/节奏决策范式

检索用途：在 `edit-director` 定稿 `edit_decisions` 前参考。词条给出**剪辑决策范式**
（转场选择、节奏、切点放置、音画同步），LLM 结合 `edit_decision_advisor` 的确定性建议
改编采纳。

## 硬约束（务必遵守）

- **edits 分类不进确定性代码注入**：仅经 `prompt_library_retriever` 由 LLM 手动检索参考，
  不进入 `shot_prompt_builder` 的 `enrich_*` 注入白名单。
- **只写方法/范式，不写成品剪辑单**：词条是决策方法论，不是可照抄的 cut 列表。
- 检索关键词放 `tags`、范式正文放 `prompt`、`notes` 只放反例/约束。

## 转场选择 (Transitions)

- id: `edits/transition-dissolve`
  category: edits
  title: 叠化转场（dissolve）
  tags: [剪辑, 转场, 叠化, dissolve, 时间流逝, 情绪转换]
  emotion: 柔顺 / 梦境 / 回忆
  action_density: low
  shot_kind: video
  prompt: "叠化转场：两个画面交叉溶解，用于时间流逝、地点切换、回忆/梦境进入、情绪缓慢转换；时长 0.5-1.5s，越慢越梦幻，避免连续叠化造成拖沓"
  notes: 反例：动作戏/快节奏段落勿用长叠化（削弱冲击）；两个强主体画面叠化易脏

- id: `edits/transition-fade-black`
  category: edits
  title: 淡入淡出/黑场转场
  tags: [剪辑, 转场, 黑场, fade, 章节, 结束]
  emotion: 完结 / 停顿 / 庄严
  action_density: low
  shot_kind: video
  prompt: "黑场转场：画面淡出至黑再淡入下一画面，用于大章节切换、开场/收尾、情绪停顿；明确分隔两个叙事段落，营造仪式感与呼吸感"
  notes: 反例：每个镜头都用黑场会拖慢节奏（短片段落内勿滥用）；避免黑场过长打断流畅感

- id: `edits/transition-wipe-push`
  category: edits
  title: 擦除/推挤转场（wipe/push）
  tags: [剪辑, 转场, 擦除, wipe, 推挤, 地点切换, 并列]
  emotion: 明快 / 并列 / 前进
  action_density: medium
  shot_kind: video
  prompt: "擦除/推挤转场：画面从一侧被新画面擦除或推挤替换，用于并列对比、地点快速切换、节奏明快段落；可配方向（左/右/上/下）增强空间动势"
  notes: 反例：纪录片/克制风格禁用（见 style 白名单）；同一段落内方向混乱会眩晕

- id: `edits/transition-zoom-punch`
  category: edits
  title: 缩放冲击转场（zoom/punch）
  tags: [剪辑, 转场, 缩放, zoom, 冲击, 强调]
  emotion: 冲击 / 强调 / 高潮
  action_density: high
  shot_kind: video
  prompt: "缩放冲击转场：放大/缩小到新画面，用于强调关键点、情绪爆点、快节奏段落；适合配鼓点/音效落地，增强画面冲击力"
  notes: 反例：纪录片/克制风格禁用；连续用缩放冲击易疲劳，每个段落限 1-2 次

## 节奏 (Pacing)

- id: `edits/pacing-beat-sync`
  category: edits
  title: 节拍同步剪辑
  tags: [剪辑, 节奏, 节拍, 音乐, 卡点, beat-sync]
  emotion: 律动 / 高燃 / 契合
  action_density: high
  shot_kind: video
  prompt: "节拍同步：分析音乐/BGM 的节拍与能量起伏，把切点放在强拍/重音/能量爆发点；低能量段用长镜头，高能量段用快切；切点与节拍对齐营造律动感"
  notes: 反例：无音乐段落硬卡节拍（无依据）；全程对拍但内容单调（节奏≠重复）

- id: `edits/pacing-rhythm-curve`
  category: edits
  title: 节奏曲线（松紧交替）
  tags: [剪辑, 节奏, 曲线, 松紧, 起伏, pacing]
  emotion: 起伏 / 张力
  action_density: medium
  shot_kind: video
  prompt: "节奏曲线：剪辑节奏应松紧交替——紧张段落快切缩短镜头，舒缓段落长镜头留白；高潮前放缓蓄力、高潮后短暂余韵；避免全程同速的平铺"
  notes: 反例：全程快切（观众疲劳）；全程长镜头（拖沓无张力）

## 切点放置 (Cut Placement)

- id: `edits/cut-on-motion`
  category: edits
  title: 动作中切点
  tags: [剪辑, 切点, 动作, 运动中, 掩藏, cut-on-motion]
  emotion: 流畅 / 隐藏
  action_density: high
  shot_kind: video
  prompt: "动作中切点：把切点放在人物/物体运动的中间帧（转身、挥手、走过遮挡），用动作掩藏剪切，使转场流畅无跳感；动作幅度越大越容易隐藏剪切"
  notes: 反例：动作完全静止时硬切（跳轴感明显）；切点卡在动作起止帧（顿挫）

- id: `edits/cut-on-beat-emotion`
  category: edits
  title: 情绪/重音切点
  tags: [剪辑, 切点, 情绪, 重音, 台词, 音效]
  emotion: 共鸣 / 强调
  action_density: low
  shot_kind: video
  prompt: "情绪切点：切点放在情绪兑现处（台词落点、音效/重音落地、人物反应瞬间），让画面切换与情感/声音同步强化；对白切换用 L-cut/J-cut 平滑"
  notes: 反例：台词中间硬切（破坏语义）；情绪高点前不给反应镜头（观众无共鸣）

## 音画同步 (AV Sync)

- id: `edits/audio-lead-lag`
  category: edits
  title: 音画错位（L-cut / J-cut）
  tags: [剪辑, 音画, 错位, L-cut, J-cut, 声音先行]
  emotion: 流畅 / 悬念
  action_density: low
  shot_kind: video
  prompt: "音画错位：L-cut 让上一镜头的声音延续到下一镜头画面（余韵），J-cut 让下一镜头的声音先入（悬念）；用于对话切换、情绪延续、场景预告"
  notes: 反例：无过渡地随意错位（混乱）；对白重音处错位过度（听感错乱）
