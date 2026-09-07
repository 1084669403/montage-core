# Directors — 导演风格/类型范式

检索用途：在 `script-director` 生成剧本/对白、`scene-director` 规划镜头时，根据题材/情绪匹配
「类型化导演范式」作为风格参考。词条给出**可执行的风格参数 + 反例机制**，LLM 需改编而非照抄。

## 硬约束（务必遵守）

- **禁止在最终 prompt / 剧本对白中出现导演名**（如「诺兰风」「王家卫式」）。导演名仅作内部检索
  参考，绝不进入交付物。与 `shot_prompts.schema.json` 的 `cinematography.style_reference`
  契约一致：该字段明说 "NEVER written into final prompt"。
- 只写**风格参数/镜头语言范式**，不写可照抄的台词或具体镜头设计。
- `notes` 只放反例/约束（「此风格不是 X」「避免照抄」），不承担检索。

## 悬疑/惊悚 (Suspense / Thriller)

- id: `directors/suspense-classic`
  category: directors
  title: 经典悬疑范式
  tags: [导演, 悬疑, 惊悚, 悬念, mystery, suspense, thriller]
  emotion: 紧张 / 疑虑 / 压迫
  action_density: medium
  shot_kind: both
  prompt: "悬疑范式：慢推近强调疑点（dolly-in）、低角度制造压迫、过肩镜建立对峙；信息逐步释放而非一次性揭示；用主观视角限制观众所知，制造信息差"
  notes: 反例：不要在开场就揭示全部真相（信息差是核心）；避免滥用惊吓 jump-scare，悬疑靠信息控制而非音效

- id: `directors/noir-shadow`
  category: directors
  title: 黑色电影/暗影范式
  tags: [导演, 黑色电影, noir, 暗影, 冷调, 烟]
  emotion: 阴郁 / 宿命 / 冷峻
  action_density: medium
  shot_kind: both
  prompt: "黑色电影范式：高反差布光、百叶窗影、烟与雾气、雨夜湿漉街面；人物常背光或半脸隐于阴影，命运感与道德灰色地带"
  notes: 反例：避免高饱和暖色调（黑色电影是低饱和冷调）；避免明亮顶光破坏硬阴影

## 史诗/宏大 (Epic)

- id: `directors/epic-landscape`
  category: directors
  title: 史诗宏大范式
  tags: [导演, 史诗, 宏大, 壮阔, epic, 战争, 征程]
  emotion: 崇高 / 悲壮 / 开阔
  action_density: high
  shot_kind: both
  prompt: "史诗范式：远景建立世界观、航拍/摇臂升格呈现气势、人物在宏大环境中小而突出；用固定宽画幅强调空间纵深，配辽阔自然或浩大战场"
  notes: 反例：避免频繁手持与快切（削弱庄严感）；避免小景别堆砌（史诗靠环境建立，非靠特写）

## 纪实/克制 (Documentary)

- id: `directors/documentary-restraint`
  category: directors
  title: 纪实克制范式
  tags: [导演, 纪实, 克制, documentary, 手持, 自然光]
  emotion: 真实 / 冷静 / 旁观
  action_density: low
  shot_kind: both
  prompt: "纪实范式：手持/斯坦尼康跟拍、自然光、长镜头保持时间连续；镜头语言克制不炫技，让被摄主体自然流露；避免摆拍感与戏剧化打光"
  notes: 反例：避免花哨转场与调色（纪录片禁用 wipe/push/glitch 等炫技转场）；避免导演干预画面内的真实性

## 浪漫/唯美 (Romantic)

- id: `directors/romance-soft`
  category: directors
  title: 浪漫唯美范式
  tags: [导演, 浪漫, 唯美, 爱情, romance, 暖调]
  emotion: 温暖 / 心动 / 细腻
  action_density: low
  shot_kind: both
  prompt: "浪漫范式：浅景深大光圈、暖调柔和光、慢动作捕捉细节（发丝/指尖/眼神）；特写建立亲密感，留白与逆光营造氛围"
  notes: 反例：避免高对比硬光与冷调（破坏温柔感）；避免多主体抢镜（浪漫聚焦二人）

## 动作/高燃 (Action)

- id: `directors/action-kinetic`
  category: directors
  title: 动作高燃范式
  tags: [导演, 动作, 高燃, 打斗, action, kinetic]
  emotion: 紧张 / 爆发 / 热血
  action_density: high
  shot_kind: video
  prompt: "动作范式：快切与甩镜强化打击感、升格慢动作定格关键帧、低角度增强力量；动作序列节奏由急转缓再爆发，收尾定格"
  notes: 反例：避免全程一个速度（无节奏起伏）；避免动作看不清的极速乱切（至少保留主体清晰）

## 奇幻/仙侠 (Fantasy)

- id: `directors/fantasy-xianxia`
  category: directors
  title: 奇幻仙侠范式
  tags: [导演, 奇幻, 仙侠, 修仙, 玄幻, fantasy, xianxia]
  emotion: 飘逸 / 神秘 / 升华
  action_density: medium
  shot_kind: both
  prompt: "仙侠范式：云雾缭绕的仙山宗门、御剑飞行/灵气外化的粒子特效、水墨或青绿色调；升格慢镜强调超凡脱俗，空间纵深营造秘境感"
  notes: 反例：避免西方魔法城邦混搭（保持东方仙侠语境）；避免过度写实弱化仙气
