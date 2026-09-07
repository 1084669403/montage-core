# Shots — 景别 / 运镜 / 角度 / 焦段 / 转场

检索用途：在 `asset-director` 2b 的 `cinematography` 字段（angle / camera_path /
frame_composition）产出时参考。词条给出"运镜 + 情绪效果 + 适配情节"的量化描述，
帮助 LLM 把抽象情绪翻译成可执行的镜头语言。

## 景别 (Shot Size)

- id: `shots/shot-size-wide`
  category: shots
  title: 远景/全景
  tags: [景别, 全景, 远景, establishing, wide]
  emotion: 开阔 / 孤寂 / 史诗
  action_density: low
  shot_kind: first_frame
  prompt: "远景/全景：人物在环境中占比小，空间感与留白明显，氛围主导；用于开场建景、孤独行走、命运转折前的铺垫"
  notes: 建立世界观、交代人物位置；静态首帧优先，避免堆砌动作

- id: `shots/shot-size-medium`
  category: shots
  title: 中景/中近景
  tags: [景别, 中景, 中近景, medium, 对话]
  emotion: 平衡 / 日常
  action_density: medium
  shot_kind: both
  prompt: "中景：半身构图，动作与表情并重；中近景（胸部以上）用于日常对白、关系升温，自然视线高度"
  notes: 双人交流、剧情推进；视频动态时叠加小幅推近增强情绪

- id: `shots/shot-size-closeup`
  category: shots
  title: 近景/大特写
  tags: [景别, 特写, closeup, 微表情, 情绪]
  emotion: 紧张 / 聚焦
  action_density: low
  shot_kind: both
  prompt: "近景：浅景深，面部占画面约80%，眼神锐利、背景虚化；大特写仅保留眼睛/嘴唇/手指细节，高反差光影，用于情绪爆发、秘密揭晓前夕"
  notes: 情绪高潮与关键反应；表情必须写到面部（皱眉/抿唇/眼神），不写抽象情绪词

- id: `shots/shot-size-ots`
  category: shots
  title: 过肩镜头
  tags: [景别, 过肩, OTS, 对峙, 对话]
  emotion: 张力 / 对峙
  action_density: medium
  shot_kind: both
  prompt: "过肩镜头：前景肩部虚化，对话对象清晰，关系张力，主观代入；用于谈判、审讯、情感对峙"
  notes: 适合双人对峙；视频动态时可用 OTS 跟拍增强空间移动

## 运镜 (Camera Movement)

- id: `shots/move-push-in`
  category: shots
  title: 推近
  tags: [运镜, 推近, push-in, dolly, 压迫, 聚焦]
  emotion: 紧张升级 / 注意集中
  action_density: medium
  shot_kind: video
  prompt: "慢速推近（dolly in）：镜头缓慢靠近主体，背景逐渐压缩，增强压迫与沉浸感；用于人物觉醒、发现线索、真相逼近"
  notes: 推近≠变焦：物理位移；真相揭露时刻可叠加"推拉变焦"制造眩晕

- id: `shots/move-pull-out`
  category: shots
  title: 拉远
  tags: [运镜, 拉远, pull-out, dolly, 疏离, 余韵]
  emotion: 孤独 / 揭示 / 余韵
  action_density: low
  shot_kind: video
  prompt: "缓慢拉远（dolly out）：镜头平稳后拉，逐渐揭示环境与关系，人物停留原地，空间吞没人像；用于结局揭晓、孤独收尾、离别"
  notes: 后撤疏离式情绪镜头；配静景空镜效果好

- id: `shots/move-orbit`
  category: shots
  title: 环绕
  tags: [运镜, 环绕, orbit, 旋转, 张力]
  emotion: 戏剧性 / 心理波动
  action_density: medium
  shot_kind: video
  prompt: "环绕（orbit）：围绕主体旋转，突出情绪张力；用于告白时刻、角色对峙、心理波动；可加轻微变焦制造失衡眩晕感"
  notes: 环境旋转虚化增强眩晕；动作密度中等，不适合高密度打斗中连续使用

- id: `shots/move-handheld`
  category: shots
  title: 手持 / 剧烈手持
  tags: [运镜, 手持, handheld, 纪实, 紧张]
  emotion: 紧张 / 真实 / 混乱
  action_density: high
  shot_kind: video
  prompt: "手持（轻微晃动）：纪实感、增强真实与紧张；剧烈手持用于战斗、恐慌、地震场景，画面明显晃动"
  notes: 动作密度高的追逐/冲突首选；避免全程手持导致眩晕

- id: `shots/move-crane`
  category: shots
  title: 升降 / 摇臂
  tags: [运镜, 升降, crane, boom, 揭示, 气势]
  emotion: 揭示全局 / 情绪释放
  action_density: medium
  shot_kind: video
  prompt: "升降（crane/boom）：机位垂直升降改变视角层级，强化气势与空间维度；用于登场亮相、场面揭示、权力关系、命运转折"
  notes: 垂直运动塑造高低权力关系；升=气势/揭示，降=压迫/聚焦

- id: `shots/move-dolly-zoom`
  category: shots
  title: 推拉变焦 / 希区柯克变焦
  tags: [运镜, 推拉变焦, dolly-zoom, vertigo, 眩晕, 崩溃]
  emotion: 眩晕 / 不安 / 精神崩溃
  action_density: medium
  shot_kind: video
  prompt: "推拉变焦（dolly zoom）：推镜同时拉焦，背景压缩主体不变；用于精神崩溃、世界观崩塌、恐惧升级；也可组合推进+变焦形成强冲击"
  notes: 只在关键时刻使用，滥用会廉价；配不安情绪词条

- id: `shots/move-whip-pan`
  category: shots
  title: 甩镜 / 疾推
  tags: [运镜, 甩镜, whip-pan, crash-zoom, 快切, 爆发]
  emotion: 能量转移 / 节奏加快
  action_density: high
  shot_kind: video
  prompt: "甩镜（whip pan）：快速摆动形成动势与切换感；疾推（crash zoom）快速推到特写用于真相揭露、危机降临"
  notes: 高动作密度段落用来制造节奏爆发；与固定镜头交替使用

- id: `shots/move-tracking`
  category: shots
  title: 跟拍 / 斯坦尼康
  tags: [运镜, 跟拍, tracking, steadicam, 沉浸, 追逐]
  emotion: 代入感 / 前行 / 沉浸
  action_density: high
  shot_kind: video
  prompt: "跟拍（tracking forward）：持续跟随主体前移，制造临场感，用于奔跑追逐、行动戏；斯坦尼康平滑跟拍用于沉浸叙事、优雅跟拍"
  notes: 追逐奔跑首选；跟拍过肩可增强对话中的空间移动

## 角度 (Camera Angle)

- id: `shots/angle-low`
  category: shots
  title: 低角度仰拍
  tags: [角度, 仰拍, low-angle, 压迫, 威严]
  emotion: 主体高大 / 力量 / 威严
  action_density: medium
  shot_kind: both
  prompt: "低角度仰拍：机位仰拍，人物更具压迫感与权威感，透视夸张；用于反派登场、胜利瞬间、英雄塑造、权力对峙"
  notes: 制造支配感；极低角度（worm's eye）用于巨物威胁、极度压迫

- id: `shots/angle-high`
  category: shots
  title: 高角度俯拍 / 鸟瞰
  tags: [角度, 俯拍, high-angle, 鸟瞰, 孤立]
  emotion: 主体渺小 / 弱势 / 全局
  action_density: low
  shot_kind: both
  prompt: "高角度俯拍：角色显得脆弱渺小，信息一览无余，用于失败时刻、被围困、孤立无援；鸟瞰垂直俯视用于群像调度、命运俯瞰"
  notes: 俯拍孤立镜头：人物被环境包围，构图留白凸显渺小

- id: `shots/angle-dutch`
  category: shots
  title: 荷兰角
  tags: [角度, 荷兰角, dutch, canted, 失衡]
  emotion: 不安 / 混乱 / 疯狂
  action_density: medium
  shot_kind: video
  prompt: "荷兰角（dutch angle）：镜头倾斜，制造失衡与不安；用于精神崩溃、悬疑氛围、混乱状态；轻度荷兰角（5-10°）用于微妙失衡"
  notes: 倾斜需克制；配手持或晃动强化不安

- id: `shots/angle-pov`
  category: shots
  title: 主观视角 POV
  tags: [角度, 主观, POV, 第一人称, 沉浸]
  emotion: 代入感 / 沉浸
  action_density: high
  shot_kind: video
  prompt: "主观视角（POV）：第一人称视角，视线驱动，沉浸感强，轻微手持感；用于追逐、发现线索、惊悚遭遇"
  notes: 恐怖/动作片首选；轻微手持让 POV 更可信

## 焦段 / 镜头 (Lens)

- id: `shots/lens-wide`
  category: shots
  title: 广角 / 超广角
  tags: [焦段, 广角, 超广角, wide, 透视]
  emotion: 空间感 / 压迫
  action_density: medium
  shot_kind: both
  prompt: "广角（24-35mm）：自然广角、轻微变形，用于环境交代、群戏；超广角（14-24mm）：强烈透视变形、空间感强，用于大场景、压迫感、动作"
  notes: 广角强化空间纵深与压迫；不适合人像特写（变形）

- id: `shots/lens-tele`
  category: shots
  title: 长焦 / 中长焦
  tags: [焦段, 长焦, telephoto, 85mm, 压缩]
  emotion: 疏离 / 分离 / 旁观
  action_density: low
  shot_kind: both
  prompt: "中长焦（85-135mm）：压缩透视、背景虚化，用于人物特写、肖像、远距离观察；长焦抽离：空间压平，人物像被世界隔开"
  notes: 长焦制造疏离/旁观感；配浅景深

- id: `shots/lens-anamorphic`
  category: shots
  title: 变形宽银幕
  tags: [焦段, 变形宽银幕, anamorphic, 电影感, 光晕]
  emotion: 电影感 / 史诗
  action_density: medium
  shot_kind: both
  prompt: "变形宽银幕（anamorphic）：椭圆焦外、水平拉丝光晕、宽画幅电影感；配 2.39:1 宽银幕画幅用于史诗感"
  notes: 提升电影质感；配胶片颗粒与暗角更佳

## 转场 (Transition)

- id: `shots/transition-dissolve`
  category: shots
  title: 叠化 / 淡入淡出
  tags: [转场, 叠化, dissolve, 淡入淡出, fade]
  emotion: 时间流逝 / 记忆 / 梦境
  action_density: low
  shot_kind: video
  prompt: "叠化（dissolve）：两画面重叠过渡，用于时间流逝、记忆、梦境；淡入淡出（fade）用于场景开始/结束、章节划分"
  notes: 中国风审美首选，诗意、梦幻；避免花哨转场

- id: `shots/transition-match-cut`
  category: shots
  title: 匹配剪辑 / 冲击切
  tags: [转场, 匹配剪辑, match-cut, 冲击切, smash]
  emotion: 震惊 / 对比 / 主题连接
  action_density: medium
  shot_kind: video
  prompt: "匹配剪辑（match cut）：形状/动作匹配转场，用于主题连接、时空跳跃；冲击切（smash cut）：从安静到爆炸式切换，用于震惊、惊吓"
  notes: 匹配剪辑是高级手法，需两画面有视觉相似点
