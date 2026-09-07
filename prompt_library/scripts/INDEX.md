# Scripts — 剧本/对白写作范式

检索用途：在 `script-director` 生成剧本与对白时参考。词条给出**中式对白写作范式 + 结构节奏方法论**，
LLM 需改编而非照抄，产出原创剧本。

## 硬约束（务必遵守）

- **verbatim 台词约束**：凡剧本/对白进入最终交付，必须完整保留用户提供的台词原文，
  不得缩写/改写/摘要（与 `provider-prompt-rules.md` 规则 6 一致）。词条只提供**写作方法**，
  不提供可照抄的成品台词。
- **禁照抄**：词条是对白/结构范式，不是成品剧本；禁止把范例句当正式台词写入剧本。
- 只写**方法/节奏/结构**，不写可照抄的具体台词。

## 结构/节奏 (Structure & Pacing)

- id: `scripts/pacing-curve`
  category: scripts
  title: 节奏曲线（起-升-暴-决）
  tags: [剧本, 节奏, 结构, 曲线, pacing, 铺垫, 高潮]
  emotion: 递进 / 张力
  action_density: high
  shot_kind: both
  prompt: "节奏曲线：起势段（前15%建立人物与冲突种子）→攀升段（30%矛盾升级、信息渐露）→风暴段（35%冲突爆发、情绪顶点）→决战段（20%对决与收束）；付费墙/悬念钩置于段与段衔接处"
  notes: 反例：避免开场直给全部背景（信息应随冲突释放）；避免高潮后拖沓（决战段应干脆收束）

- id: `scripts/three-act-frame`
  category: scripts
  title: 三幕结构（建制-对抗-解决）
  tags: [剧本, 三幕, 结构, 建置, 对抗, 解决, three-act]
  emotion: 确立 / 升级 / 收束
  action_density: medium
  shot_kind: both
  prompt: "三幕结构：第一幕建制（25%，交代主角日常与渴望，第一转折点把主角推离日常）；第二幕对抗（50%，主角主动行动但屡遭反制，中点出现重大揭示或立场反转，第二转折点把主角逼入绝境）；第三幕解决（25%，主角带着第二幕所学做最终抉择，结局回答第一幕提出的渴望是否兑现）"
  notes: 反例：避免第一幕信息过载（观众没建立情感就进入冲突）；避免第二幕主角全程被动（对抗需要主角的主动选择驱动）

- id: `scripts/hero-journey`
  category: scripts
  title: 英雄之旅骨架（出发-考验-回归）
  tags: [剧本, 英雄之旅, 结构, 弧光, 成长, hero-journey]
  emotion: 召唤 / 蜕变 / 归乡
  action_density: high
  shot_kind: both
  prompt: "英雄之旅骨架：日常世界→召唤冒险（拒绝/接受）→跨越门槛→试炼之路（盟友/敌人/导师）→最深的洞穴（直面最大恐惧）→重生（牺牲与获得）→带着奖赏归来；结构可压缩到短片（每个节点一场戏），但'出发-考验-回归'三段不可省略"
  notes: 反例：避免套模板（节点必须服务于具体故事，不能为走流程而走流程）；避免'最深的洞穴'无代价（重生必须以牺牲为代价才有分量）

- id: `scripts/character-arc`
  category: scripts
  title: 人物弧光（渴望-缺陷-转变）
  tags: [剧本, 人物, 弧光, 渴望, 缺陷, 转变, character-arc]
  emotion: 认同 / 共鸣 / 成长
  action_density: low
  shot_kind: both
  prompt: "人物弧光三件套：渴望（人物主动追求什么）、缺陷（阻碍渴望的内在信念/恐惧）、转变（结局时缺陷被现实击碎，人物做出与开场相反的选择）；弧光要可被观众验证——开场的'不敢'必须变成结局的'敢'，且转变由情节逼出而非旁白说明"
  notes: 反例：避免无缺陷的主角（完美主角无成长空间）；避免转变缺乏情节支撑（'突然想通了'是弧光失败）

- id: `scripts/hook-design`
  category: scripts
  title: 钩子设计（悬念/反转/情绪/信息/危机）
  tags: [剧本, 钩子, 悬念, 反转, 情绪, hook]
  emotion: 好奇 / 期待 / 揪心
  action_density: medium
  shot_kind: both
  prompt: "钩子系统：每集/每场结尾至少一个钩子——悬念钩（未知待解）、反转钩（预期被打破）、情绪钩（情感未兑现）、信息钩（关键信息将揭）、危机钩（危险逼近）；钩子应推动下一场而非孤立"
  notes: 反例：避免钩子与主线无关（孤立钩子浪费情绪）；避免每场都堆钩子导致观众疲惫

## 场景写作 (Scene Craft)

- id: `scripts/scene-goal-conflict`
  category: scripts
  title: 场景三要素（目标-冲突-意外）
  tags: [剧本, 场景, 目标, 冲突, 意外, scene]
  emotion: 张力 / 推进
  action_density: medium
  shot_kind: both
  prompt: "场景三要素：每场戏主角带着具体目标进场（要钱/要答案/要告别），遭遇阻碍（人物/环境/时间），并发生至少一个意外（对方不按预期回应/新信息改变局势）；没有目标的场景是废戏，没有意外的场景是过场；结尾场景状态必须与开场不同（关系/信息/立场任一改变）"
  notes: 反例：避免纯信息交代场景（信息应包在冲突里给）；避免'顺利达成'的场景（无阻碍则无戏）

- id: `scripts/dialogue-purpose`
  category: scripts
  title: 对白五用（信息/关系/情绪/伏笔/行动）
  tags: [剧本, 对白, 目的, 信息, 关系, 伏笔, dialogue]
  emotion: 层次 / 克制
  action_density: low
  shot_kind: both
  prompt: "对白五用：一段合格对白至少同时完成两件事——传递信息（推进情节）、改变关系（拉近/疏远/试探）、外化情绪（不说'我生气'而用动作与潜台词）、埋设伏笔（看似闲笔实则关键）、驱动行动（引出下一场）；写对白时给每句标'目的'，删掉无目的句"
  notes: 反例：避免对白只完成一件事（单薄）；避免全员说同一腔调（人物身份/性格/处境决定用词与句式）

## 情绪兑现 (Emotion Payoff)

- id: `scripts/dialogue-subtext`
  category: scripts
  title: 对白潜台词
  tags: [对白, 潜台词, 潜文本, 情绪, dialogue, subtext]
  emotion: 克制 / 张力
  action_density: low
  shot_kind: both
  prompt: "对白潜台词：表面话不直接说破，情绪藏于言外——用反问、欲言又止、转移话题、口是心非表达真实情绪；信息通过潜文本传递而非直白陈述"
  notes: 反例：避免对白直白报情绪（'我很生气'是差对白）；避免所有角色说话方式相同（需区分身份/性格）

- id: `scripts/dialogue-action-drive`
  category: scripts
  title: 对白推动行动
  tags: [对白, 行动, 冲突, 驱动, dialogue, conflict]
  emotion: 对抗 / 坚定
  action_density: medium
  shot_kind: both
  prompt: "对白应推动行动而非纯聊天：每段对白都要改变人物关系或推进情节（谈判/请求/威胁/坦白）；对白蕴含意图与障碍，冲突由对白的内在对抗驱动"
  notes: 反例：避免纯寒暄/纯解释的冗余对白（无行动推动）；避免一人长篇独白破坏对话张力

## 要素模板 (Element Slots)

词条只列该风格剧本**必须填的槽**，不提供可照抄台词。检索：`要素 电影` / `要素 动漫` / `要素 漫画` / `要素 口播`。

- id: `scripts/elements-cinematic`
  category: scripts
  title: 电影要素模板（环境/人物/道具/对白/节拍）
  tags: [剧本, 要素, 电影, 槽位, cinematic, environment]
  emotion: 电影 / 叙事
  action_density: medium
  shot_kind: both
  prompt: "电影要素槽：environment（地点/空间/光线/色调/时代/氛围）必填；characters[]（appearance/outfit/speech_style 逐字锚点）叙事片必填；props[] 关键道具外观一句；structure 四拍 hook/escalation/reveal/landing；sections[].lines[] 每句带 speaker_id（禁止'有人说'）；narration 只作旁白或 lines 派生朗读稿；tone 写色调与情绪基调。对白服务冲突，环境必须可被镜头看见。"
  notes: 反例：环境只写在散文旁白里导致分镜无法读取；对白无说话人；人物外观到分镜阶段改写

- id: `scripts/elements-anime`
  category: scripts
  title: 动漫要素模板（世界观/羁绊/外观锚点）
  tags: [剧本, 要素, 动漫, 日漫, 国漫, 世界观, anime]
  emotion: 羁绊 / 热血
  action_density: high
  shot_kind: both
  prompt: "动漫要素槽：先写世界观一句（力量规则/时代锚，禁止长篇设定集）；characters[] 必填外观锚点（发色/瞳色/标志物，禁止跨镜改设定）与羁绊关系 relationships[]；environment 含标志性场景符号（校园天台/夜巷灯牌等）；lines[] 对白密度可高于电影但须装进 5 字/秒预算；每场至少一个可见动作节拍。负向：不要写实皮肤细节去冲淡角色符号。"
  notes: 反例：世界观写成说明书；角色每镜换发型/瞳色；对白超时长网格

- id: `scripts/elements-manga`
  category: scripts
  title: 漫画要素模板（分镜张力/拟声词）
  tags: [剧本, 要素, 漫画, 分镜, 拟声词, manga, panel]
  emotion: 张力 / 停格
  action_density: high
  shot_kind: both
  prompt: "漫画要素槽：environment 强调高对比光影与可读剪影；每段标出一个信息量最大的停格（hero_moment）；lines[] 短句，可在 delivery 注明拟声词/语气（拟声词不是对白正文）；props[] 写出特写道具；structure 仍用四拍但节奏更快。画面风格走描边/网点由 playbook 负责，本模板只约束剧本槽。"
  notes: 反例：把对话框入场动画写进剧本（那是后期图形镜）；拟声词塞进 narration 当旁白朗读

- id: `scripts/elements-spoken`
  category: scripts
  title: 口播要素模板（钩子/信息密度）
  tags: [剧本, 要素, 口播, 讲解, 钩子, spoken, talking-head]
  emotion: 清晰 / 钩子
  action_density: low
  shot_kind: both
  prompt: "口播要素槽：开场 3 秒内 hook（一个问题或反直觉判断）；sections[] 每段一个信息点，lines[] 默认 speaker_id=narrator；人物卡可选（出镜讲解员才需要 appearance）；environment 写清楚出镜背景（桌面/灯/干净墙），避免复杂群戏；对白按 5 字/秒算死，宁短勿堆。竖屏大字幕由 style pack 负责，剧本只保证信息可被听清。"
  notes: 反例：口播片强制四拍人物弧光；一段塞多个互不相关知识点；无钩子开场

## 情绪兑现 (Emotion Payoff)

- id: `scripts/emotion-payoff`
  category: scripts
  title: 情绪兑现
  tags: [剧本, 情绪, 兑现, 情感, emotion, payoff]
  emotion: 触动 / 共鸣
  action_density: low
  shot_kind: both
  prompt: "情绪兑现：前期埋设的情绪伏笔（人物渴望/关系裂痕/未了心愿）必须在中后期获得兑现或反转；情绪高点由铺垫累积而来，让观众共情而非被强推"
  notes: 反例：避免无铺垫的空降情绪爆发（观众无感）；避免情绪兑现后无余韵（给观众回味空间）

- id: `scripts/turn-and-reversal`
  category: scripts
  title: 转折与反转
  tags: [剧本, 转折, 反转, 意外, turn, reversal]
  emotion: 惊讶 / 震撼
  action_density: medium
  shot_kind: both
  prompt: "转折设计：关键转折须由前文伏笔合理引出（意外但不突兀）；反转改变人物或事件的意义，让观众回看前文时发现早有暗示；转折是节奏曲线风暴段的支点"
  notes: 反例：避免无伏笔的硬反转（机械降神）；避免转折后不改变故事走向（无意义反转）
