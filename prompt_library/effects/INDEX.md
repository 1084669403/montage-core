# Effects — 特效 / 粒子 / 破坏 / 能量

检索用途：在 `asset-director` 2b 产出 `visual_details.environment` / `lighting` / 动作节拍时，
为特效类镜头（爆炸/粒子/流体/能量/破坏/转场特效）提供量化 Prompt 参考。词条含
「类型 + 物理行为 + 光影特征 + 数量密度」的可执行描述。

## 自然粒子 (Natural Particles)

- id: `effects/particle-rain`
  category: effects
  title: 雨 / 雨夜
  tags: [特效, 雨, 雨夜, 水滴, rain, 水花]
  emotion: 压抑 / 紧张 / 诗意
  action_density: medium
  shot_kind: both
  prompt: "雨滴直线下落、遇面飞溅，逆光下发光、有运动模糊；雨夜街面湿润反光，霓虹灯色在积水里拉出光斑；水花撞击后放射状飞散"
  notes: 逆光/霓虹反光提升氛围；雨夜常配赛博朋克/悬疑/悲剧；SFX 配雨声+水花声

- id: `effects/particle-snow`
  category: effects
  title: 雪 / 飘雪
  tags: [特效, 雪, 雪花, snow, 冬季]
  emotion: 静谧 / 纯净 / 寂寥
  action_density: low
  shot_kind: both
  prompt: "雪花飘落摇摆、速度慢、堆积，柔散射、无硬影、体积感；雪夜极光或冷蓝月光下雪面反光"
  notes: 静谧/治愈/圣诞/离别场景；配冷调光线

- id: `effects/particle-ember`
  category: effects
  title: 火星/余烬
  tags: [特效, 火星, 余烬, ember, 燃烧, 暖光]
  emotion: 温暖 / 危险 / 末日
  action_density: medium
  shot_kind: video
  prompt: "火星/余烬螺旋上升、渐暗消散，暖橙色自发光、明暗闪烁；火光边缘勾勒物体轮廓"
  notes: 火灾/末日/废墟/篝火场景；配暖调高对比

- id: `effects/particle-dust`
  category: effects
  title: 尘埃/丁达尔光束
  tags: [特效, 尘埃, 光束, 丁达尔, dust, volumetric]
  emotion: 静谧 / 神秘 / 时间感
  action_density: low
  shot_kind: first_frame
  prompt: "尘埃悬浮漂浮，在逆光下形成可见光束（丁达尔效应/体积光）；烟雾湍流上升、半透明、遮挡与半遮挡"
  notes: 空镜/回忆/神秘场景；体积光是高级氛围元素，配窗口光或逆光

- id: `effects/particle-petal`
  category: effects
  title: 花瓣/落叶
  tags: [特效, 花瓣, 落叶, 飘落, petal, 樱花]
  emotion: 唯美 / 浪漫 / 哀婉
  action_density: low
  shot_kind: video
  prompt: "花瓣/落叶摇摆飘落、受风影响，逆光半透明、色彩鲜明；樱花飘落铺满地面，风起时卷成旋"
  notes: 爱情/离别/春季/唯美场景；配逆光与慢速飘落

## 能量/魔法粒子 (Energy / Magic)

- id: `effects/energy-magic`
  category: effects
  title: 魔法/能量粒子
  tags: [特效, 魔法, 能量, 灵气, 法术, magic]
  emotion: 神秘 / 神圣 / 奇幻
  action_density: high
  shot_kind: video
  prompt: "灵气/魔法粒子：缓飘围绕主体、微光自发光，暖金或冷蓝；魔法粒子跟随手势/法术轨迹，颜色=魔法属性；能量电弧呈闪电状分叉、瞬间极亮白/蓝、频闪残影"
  notes: 修仙/奇幻/科幻；能量电弧配频闪与残影，SFX 配能量嗡鸣

- id: `effects/energy-glow-trail`
  category: effects
  title: 光翼/光迹拖尾
  tags: [特效, 光迹, 拖尾, 光翼, glow, trail]
  emotion: 速度 / 神圣 / 战斗
  action_density: high
  shot_kind: video
  prompt: "角色动作拖曳光尾：渐变光、逐渐消散；速度/神圣/战斗场景，光迹颜色与角色能量属性一致"
  notes: 高速移动/冲刺/施法；配慢动作或子弹时间增强

- id: `effects/energy-digital`
  category: effects
  title: 数据粒子/数字世界
  tags: [特效, 数据粒子, 数字, 像素, 赛博, digital]
  emotion: 科技 / 赛博 / 未来
  action_density: medium
  shot_kind: both
  prompt: "数据粒子：像素/数字流、直线运动、数码青/绿、矩阵排列；数字瓦解：像素/网格逐层消散、粒子化、褪色"
  notes: 赛博/数字世界/黑客/转场到数字维度；配霓虹与 HUD 元素

## 破坏/破碎 (Destruction)

- id: `effects/destroy-glass`
  category: effects
  title: 玻璃破碎
  tags: [特效, 玻璃, 破碎, 碎片, glass, 动作]
  emotion: 冲击 / 爆发 / 危险
  action_density: high
  shot_kind: video
  prompt: "玻璃从撞击点放射状裂开→落下，锐利碎片、逆光反射；碎片高速飞散、二次撞击，慢动作可见"
  notes: 动作戏/追逐/危机场景；配碎片音效与慢动作

- id: `effects/destroy-explosion`
  category: effects
  title: 爆炸/火焰
  tags: [特效, 爆炸, 火焰, 爆炸碎片, explosion, fire]
  emotion: 冲击 / 灾难 / 高燃
  action_density: high
  shot_kind: video
  prompt: "爆炸：火焰包裹的碎片从爆炸中心向外飞散、高速、二次撞击；真实火光+数字扩规模；冲击波激起尘埃与灰烬；逆光剪影强化轮廓"
  notes: 大破坏场景实拍+数字增强；动作密度最高；配低频冲击音效；建议长镜头规避物理崩坏

- id: `effects/destroy-collapse`
  category: effects
  title: 崩塌/墙体碎裂
  tags: [特效, 崩塌, 坍塌, 碎裂, collapse, rubble]
  emotion: 灾难 / 末日 / 震撼
  action_density: high
  shot_kind: video
  prompt: "石墙裂缝扩散→块状坍塌，重力下落、粉尘扬起、尘土云翻滚；废墟倒塌掀起烟尘遮蔽视线"
  notes: 末日/灾难/战斗场景；配低频轰鸣+尘土弥漫

- id: `effects/destroy-pixel`
  category: effects
  title: 数字瓦解
  tags: [特效, 瓦解, 像素化, 消散, pixel]
  emotion: 超现实 / 消逝 / 转场
  action_density: medium
  shot_kind: video
  prompt: "数字瓦解：像素/网格逐层消散、粒子化、数据流、褪色；用于维度转换/角色死亡/转场到数字世界"
  notes: 高级转场/特效叙事手法；配数据粒子与数码色调

## 特效叙事 (VFX Narrative)

- id: `effects/vfx-narrative-emotion`
  category: effects
  title: 情绪外化特效
  tags: [特效, 情绪外化, 愤怒, 悲伤, 特效叙事, emotion]
  emotion: 依场景而定
  action_density: high
  shot_kind: video
  prompt: "情绪外化：角色内心状态通过环境特效外化——愤怒=周围起火/能量波动，悲伤=下雨/花瓣凋零，崩溃=周围崩塌/粒子瓦解"
  notes: 高级导演手法，把抽象情绪翻译成可见特效；需与角色表情/动作一致
