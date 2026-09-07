# Lighting — 光线 / 色调 / 风格化影调

检索用途：在 `asset-director` 2b 产出 `visual_details.lighting` 与首帧图提示词时参考。
词条给出"光线类型 + 效果 + 适配情绪"的可执行描述，避免抽象词（"氛围感"→"低角度侧逆光+轮廓光"）。

## 时间段光线 (Time-of-Day)

- id: `lighting/time-golden-hour`
  category: lighting
  title: 黄金时刻
  tags: [光线, 黄金时刻, golden-hour, 日落, 暖光]
  emotion: 温暖 / 浪漫 / 怀旧
  action_density: medium
  shot_kind: both
  prompt: "黄金时刻光线：暖色阳光低角度斜射，长阴影，金色高光；黄昏侧逆光勾勒轮廓，配薄雾或尘埃更有层次"
  notes: 情感/浪漫/怀旧/英雄时刻；黄金时刻人物轮廓光极佳

- id: `lighting/time-blue-hour`
  category: lighting
  title: 蓝调时刻
  tags: [光线, 蓝调时刻, blue-hour, 暮色, 冷调]
  emotion: 冷静 / 忧郁 / 神秘
  action_density: low
  shot_kind: both
  prompt: "蓝调时刻：暮色渐暗天空呈冷蓝，地面暗部偏蓝，城市灯光渐亮形成冷暖对比"
  notes: 悬疑/忧郁/都市夜景前奏；冷暖对比增强层次

- id: `lighting/time-dawn`
  category: lighting
  title: 黎明破晓
  tags: [光线, 黎明, 破晓, dawn, 晨光]
  emotion: 希望 / 新生 / 平静
  action_density: low
  shot_kind: first_frame
  prompt: "黎明破晓光：低角度晨光穿透薄雾，天色由深蓝转暖橙，地面湿润反光，轮廓被柔和金边勾勒"
  notes: 新生/希望/平静场景；配薄雾与丁达尔光束

- id: `lighting/time-noon`
  category: lighting
  title: 正午顶光
  tags: [光线, 正午, 顶光, noon, 硬光]
  emotion: 现实 / 纪实 / 压抑
  action_density: medium
  shot_kind: both
  prompt: "正午顶光：正上方强烈日光，阴影短而深、高对比，肤色发亮，偏纪实感"
  notes: 纪实/压抑/现实场景；顶光易显僵硬，慎用于唯美画面

## 室内光 / 戏剧光 (Indoor / Dramatic)

- id: `lighting/indoor-window`
  category: lighting
  title: 窗户自然光
  tags: [光线, 窗户光, 自然光, 明暗对比, window, chiaroscuro]
  emotion: 沉思 / 静谧 / 情绪化
  action_density: low
  shot_kind: first_frame
  prompt: "窗户自然光（明暗对比 chiaroscuro）：单侧窗光形成强烈明暗对比，暗部沉入阴影，亮部暖光；光线在人物脸部形成伦勃朗式三角光"
  notes: 沉思/独白/情绪化场景；高级氛围光，首帧图首选

- id: `lighting/indoor-neon`
  category: lighting
  title: 霓虹灯补光
  tags: [光线, 霓虹, 霓虹灯, neon, 赛博朋克]
  emotion: 赛博 / 冷峻 / 城市夜
  action_density: medium
  shot_kind: both
  prompt: "霓虹灯补光：品红+青蓝色霓虹光从两侧打向人物，脸部形成红蓝分色光，背景霓虹招牌虚化光斑"
  notes: 赛博朋克/雨夜/都市夜景；红蓝分色是标志性赛博光

- id: `lighting/indoor-tungsten`
  category: lighting
  title: 钨丝灯暖光
  tags: [光线, 钨丝灯, 暖光, 烛光, 复古, tungsten]
  emotion: 温暖 / 复古 / 亲密
  action_density: low
  shot_kind: both
  prompt: "钨丝灯暖光：暖橙色单一光源，皮肤呈琥珀色，阴影柔中带硬；烛光：摇曳的暖光、微光闪烁、暗部更暗"
  notes: 复古/亲密/睡前/老式室内；配胶片颗粒增强复古感

- id: `lighting/indoor-fluorescent`
  category: lighting
  title: 荧光灯冷光
  tags: [光线, 荧光灯, 冷光, 医院, 办公, fluorescent]
  emotion: 冷峻 / 压抑 / 现实
  action_density: low
  shot_kind: both
  prompt: "荧光灯冷光：均匀偏冷的绿白光线，色彩去饱和、阴影平而少，偏纪实/压抑氛围"
  notes: 医院/办公室/审讯室/现实场景；冷调压抑

## 戏剧光 / 风格光 (Dramatic / Stylized)

- id: `lighting/drama-rembrandt`
  category: lighting
  title: 伦勃朗光
  tags: [光线, 伦勃朗光, rembrandt, 肖像, 三角光]
  emotion: 深邃 / 经典 / 张力
  action_density: low
  shot_kind: first_frame
  prompt: "伦勃朗光：侧上方主光在脸颊形成经典三角光斑，另一侧陷入阴影，经典肖像质感"
  notes: 肖像/英雄/权力/深沉人物；配深色背景

- id: `lighting/drama-rim`
  category: lighting
  title: 轮廓光/边缘光
  tags: [光线, 轮廓光, 边缘光, rim, 逆光, 剪影]
  emotion: 神圣 / 决绝 / 神秘
  action_density: medium
  shot_kind: both
  prompt: "轮廓光（rim light）：光源在主体背后，人物轮廓边缘清晰发亮；强逆光剪影：面部隐藏于黑影、轮廓发光，配大气薄雾增强边缘光"
  notes: 离别/牺牲/最终抉择/神秘出场；剪影决绝镜头经典配置

- id: `lighting/drama-volumetric`
  category: lighting
  title: 体积光
  tags: [光线, 体积光, 丁达尔, volumetric, 光柱]
  emotion: 神圣 / 静谧 / 神秘
  action_density: low
  shot_kind: first_frame
  prompt: "体积光（volumetric）：可见光柱穿过薄雾/尘埃/窗棂，光线成束、丁达尔效应，尘埃在光束中漂浮"
  notes: 教堂/森林/阁楼/梦境；营造神圣与神秘氛围

- id: `lighting/drama-low-key`
  category: lighting
  title: 低角度侧逆光/低键光
  tags: [光线, 低键, 高对比, 深阴影, low-key, 压迫]
  emotion: 压抑 / 悬疑 / 紧张
  action_density: medium
  shot_kind: both
  prompt: "低键光：暗部大范围沉入阴影，单光源高对比，亮部集中；深阴影配窄亮区，制造压迫与悬疑"
  notes: 悬疑/恐怖/悲剧/压迫场景；暗部细节适度保留避免死黑

## 色调 / 色彩影调 (Color Grading)

- id: `lighting/grade-teal-orange`
  category: lighting
  title: 青橙对比
  tags: [光线, 青橙, teal-orange, 电影调色, 对比]
  emotion: 电影感 / 商业 / 冲击
  action_density: medium
  shot_kind: both
  prompt: "青橙对比（teal & orange）：阴影偏青蓝、高光偏暖橙，电影最常用的互补色，画面通透、人物肤色突出"
  notes: 电影/商业/动作片通用；避免过度饱和

- id: `lighting/grade-cool-muted`
  category: lighting
  title: 冷调去饱和
  tags: [光线, 冷调, 去饱和, 蓝灰, 北欧, cool]
  emotion: 冷静 / 疏离 / 阴郁
  action_density: low
  shot_kind: both
  prompt: "冷调去饱和：青蓝冷调、去饱和、蓝灰影调、北欧冷白；雪原/峡湾/雨夜/抑郁情绪"
  notes: 北欧冷调/悬疑/阴郁/工业感；配冷色环境

- id: `lighting/grade-warm-vintage`
  category: lighting
  title: 复古暖橙
  tags: [光线, 复古, 暖橙, 胶片, 怀旧, vintage]
  emotion: 怀旧 / 温暖 / 复古
  action_density: low
  shot_kind: first_frame
  prompt: "复古暖橙：暖黄高光、琥珀色调、黄油色调，配胶片颗粒与轻微暗角，复古怀旧质感"
  notes: 80年代/老照片/怀旧回忆；配复古胶片设备词条

- id: `lighting/grade-bw-highcontrast`
  category: lighting
  title: 黑白高反差
  tags: [光线, 黑白, 高反差, 单色, bw]
  emotion: 纪实 / 严肃 / 经典
  action_density: medium
  shot_kind: both
  prompt: "黑白高反差：单色、极强明暗对比，暗部纯黑、亮部刺白，纪实摄影/经典电影感"
  notes: 纪实/黑色电影/严肃题材；配胶片颗粒
