# Scenes — 场景 / 环境

检索用途：在 `scene-director` 场景分解与 `asset-director` 2b 产出 `visual_details.environment`
时参考。词条给出「核心视觉元素 + 氛围 + 光影 + 色彩」的可执行描述，
帮助 LLM 把场景地点翻译成可视化画面，并保持全片视觉一致。

## 自然景观 (Natural)

- id: `scenes/mountain-cloudsea`
  category: scenes
  title: 高山云海
  tags: [场景, 山, 云海, 仙境, 悬崖, mountain]
  emotion: 壮阔 / 仙气 / 高远
  action_density: low
  shot_kind: first_frame
  prompt: "山峰穿云而出，翻涌云海覆盖山谷，悬崖峭壁如刀削，远处孤峰隐约；阳光穿透云层形成金色光柱，色彩青白+金"
  notes: 仙侠/史诗开场；配低机位航拍与体积光

- id: `scenes/forest-valley`
  category: scenes
  title: 密林深谷
  tags: [场景, 森林, 深谷, 神秘, 幽暗, forest]
  emotion: 幽深 / 神秘 / 危险
  action_density: medium
  shot_kind: both
  prompt: "参天巨树、藤蔓缠绕、幽暗谷底、光线斑驳；树冠漏光、苔藓微光，色彩深绿/墨绿"
  notes: 冒险/奇幻/潜入场景；配体积光与雾

- id: `scenes/desert-gobi`
  category: scenes
  title: 沙漠戈壁
  tags: [场景, 沙漠, 戈壁, 荒芜, 雅丹, desert]
  emotion: 荒芜 / 残酷 / 孤独
  action_density: medium
  shot_kind: both
  prompt: "无垠沙海、风蚀雅丹、枯骨残骸、海市蜃楼；烈日顶光/黄昏金橙/星空冷蓝，色彩金黄/暗橙"
  notes: 末日/武侠/西部场景；黄昏长阴影强化荒凉

- id: `scenes/ocean-deep`
  category: scenes
  title: 深海幽蓝
  tags: [场景, 深海, 海洋, 荧光, 沉船, ocean]
  emotion: 幽闭 / 神秘 / 恐惧
  action_density: low
  shot_kind: both
  prompt: "深海暗蓝、生物荧光、珊瑚礁群、沉船残骸；深海微弱蓝光、生物荧光点缀，色彩深蓝/荧光绿"
  notes: 科幻/冒险/水下文戏；配水下光折射

- id: `scenes/glacier-snowfield`
  category: scenes
  title: 冰川雪原
  tags: [场景, 冰川, 雪原, 极光, 暴风雪, glacier]
  emotion: 纯净 / 孤独 / 严酷
  action_density: medium
  shot_kind: both
  prompt: "冰川裂隙、蓝色冰层、极光、暴风雪；冰面折射、极光漫射、雪光反射，色彩冰蓝/纯白"
  notes: 北欧/极地/末世场景；配冷调去饱和与雪特效

- id: `scenes/grassland`
  category: scenes
  title: 草原辽阔
  tags: [场景, 草原, 地平线, 马群, 部落, grassland]
  emotion: 自由 / 辽阔 / 苍茫
  action_density: medium
  shot_kind: both
  prompt: "无边草海、远山地平线、蒙古包、马群；日出日落金光、暴风雨前灰调，色彩草绿/天蓝"
  notes: 史诗/部落/自由主题；配大远景与黄金时刻

- id: `scenes/volcano`
  category: scenes
  title: 火山熔岩
  tags: [场景, 火山, 熔岩, 岩浆, 灾难, volcano]
  emotion: 毁灭 / 力量 / 危险
  action_density: high
  shot_kind: both
  prompt: "翻滚岩浆、黑烟柱、熔岩河、火山口；岩浆红光、黑烟遮天、火花飞溅，色彩暗红/黑"
  notes: 灾难/战斗/世界终结场景；配火星余烬特效与低频音效

- id: `scenes/waterfall`
  category: scenes
  title: 瀑布飞流
  tags: [场景, 瀑布, 水雾, 彩虹, 深潭, waterfall]
  emotion: 壮观 / 清新 / 力量
  action_density: medium
  shot_kind: both
  prompt: "百丈瀑布、水雾彩虹、深潭、湿滑岩石；水雾散射光、彩虹折射，色彩白/青/彩虹色"
  notes: 仙侠/自然/净化重生场景；配慢速流水与白噪

- id: `scenes/flower-field`
  category: scenes
  title: 花田花海
  tags: [场景, 花海, 花田, 浪漫, 治愈, flower]
  emotion: 浪漫 / 治愈 / 梦幻
  action_density: low
  shot_kind: first_frame
  prompt: "无边花海、风过花浪、蝴蝶蜜蜂、小径蜿蜒；阳光柔和、逆光发丝、花瓣反光，色彩粉/紫/黄"
  notes: 爱情/治愈/婚礼场景；配花瓣特效与柔光

- id: `scenes/lake-moonlight`
  category: scenes
  title: 湖泊月色
  tags: [场景, 湖, 月色, 芦苇, 倒影, lake]
  emotion: 宁静 / 诗意 / 孤独
  action_density: low
  shot_kind: first_frame
  prompt: "平静湖面、月影倒映、芦苇荡、远山轮廓；月光银白、水面反射、芦苇暗影，色彩银白/深蓝"
  notes: 文艺/武侠/独白场景；配蓝调时刻与空镜余韵

- id: `scenes/canyon`
  category: scenes
  title: 峡谷深渊
  tags: [场景, 峡谷, 深渊, 一线天, 险峻, canyon]
  emotion: 压迫 / 险峻 / 幽暗
  action_density: medium
  shot_kind: both
  prompt: "两侧绝壁、一线天、深渊底部、藤蔓垂落；谷底幽暗、一线天光，色彩暗灰/一线白"
  notes: 冒险/武侠/追击场景；配纵深镜头与光影切割

## 城市景观 (Urban)

- id: `scenes/city-china-modern`
  category: scenes
  title: 中国现代都市
  tags: [场景, 城市, 现代都市, 摩天楼, 车流, city]
  emotion: 繁华 / 喧嚣 / 速度
  action_density: high
  shot_kind: both
  prompt: "玻璃幕墙高楼、霓虹中文招牌、天桥、车流；霓虹混合光、LED屏幕、路灯暖光，色彩霓虹混色"
  notes: 都市/现实/商业场景；配延时摄影感光轨

- id: `scenes/city-cyberpunk`
  category: scenes
  title: 赛博朋克都市
  tags: [场景, 赛博朋克, 霓虹, 全息, 雨夜, cyberpunk]
  emotion: 高科技低生活 / 赛博 / 迷幻
  action_density: high
  shot_kind: both
  medium: film,anime
  genre: sci-fi
  prompt: "巨型全息投影、密集霓虹灯、雨夜、贫民窟叠高楼；霓虹冷光、全息投影光、雨面反射，色彩赛博青+品红"
  notes: 科幻/赛博/黑客题材；配霓虹补光、雨夜、数据粒子

- id: `scenes/city-old-shanghai`
  category: scenes
  title: 老上海/民国
  tags: [场景, 老上海, 民国, 石库门, 弄堂, 年代]
  emotion: 怀旧 / 风情 / 暗流
  action_density: medium
  shot_kind: both
  prompt: "石库门、弄堂、有轨电车、霓虹招牌、黄包车；钨丝灯暖光、霓虹橙、月光，色彩暖橙/深绿"
  notes: 年代/谍战/民国题材；配复古暖橙与胶片噪点

- id: `scenes/city-hk`
  category: scenes
  title: 香港窄街
  tags: [场景, 香港, 窄街, 霓虹招牌, 茶餐厅, 雨夜]
  emotion: 拥挤 / 烟火 / 孤独
  action_density: medium
  shot_kind: both
  prompt: "密集招牌叠招牌、窄巷、晾衣竹、茶餐厅；霓虹拖影、雨夜反光、室内暖光，色彩霓虹蓝+暖橙"
  notes: 都市/孤独/警匪题材；配王家卫式霓虹拖影与雨夜

- id: `scenes/city-tokyo`
  category: scenes
  title: 日本东京
  tags: [场景, 东京, 十字路口, 居酒屋, 新干线, tokyo]
  emotion: 现代 / 秩序 / 都市
  action_density: medium
  shot_kind: both
  prompt: "十字路口人群、自动贩卖机、居酒屋灯笼、新干线；LED白、灯笼暖橙，色彩白+暖橙"
  notes: 现代/日常/治愈系；配日系清新色调

- id: `scenes/city-europe`
  category: scenes
  title: 欧洲古城
  tags: [场景, 欧洲, 古城, 哥特, 教堂, 石板路]
  emotion: 历史 / 优雅 / 浪漫
  action_density: medium
  shot_kind: both
  prompt: "石板路、哥特教堂、广场、拱廊、喷泉；自然散射、教堂彩色玻璃光，色彩石色/金"
  notes: 欧洲/历史/奇幻题材；配暖色调与柔和散射光

- id: `scenes/city-wasteland`
  category: scenes
  title: 废土城市
  tags: [场景, 废土, 废墟, 末日, 残骸, wasteland]
  emotion: 荒芜 / 末日 / 绝望
  action_density: high
  shot_kind: both
  prompt: "废墟残垣、钢筋裸露、杂草丛生、车辆残骸；昏黄天光、火光、月光，色彩灰烬灰/血锈红"
  notes: 末日/生存/战争题材；配废墟纪实与火星余烬

- id: `scenes/city-future`
  category: scenes
  title: 未来都市
  tags: [场景, 未来都市, 悬浮建筑, 飞行汽车, 乌托邦, future]
  emotion: 科技 / 乌托邦 / 清洁
  action_density: medium
  shot_kind: both
  prompt: "悬浮建筑、透明管道、空中花园、飞行汽车；LED冷白、自然光透过穹顶，色彩白+银+青"
  notes: 科幻/未来/乌托邦题材；配航拍穿越镜头

## 建筑空间 (Architecture / Interior)

- id: `scenes/immortal-sect`
  category: scenes
  title: 仙山宗门
  tags: [场景, 仙山, 宗门, 修仙, 云海, 宫殿]
  emotion: 仙气 / 宏大 / 神圣
  action_density: low
  shot_kind: first_frame
  prompt: "悬浮山峰、云海、飞桥、宫殿群、灵柱；天光+灵气自发光+金光，色彩青白/金"
  notes: 仙侠/修仙题材；配高山云海与体积光

- id: `scenes/ancient-palace`
  category: scenes
  title: 古代宫殿
  tags: [场景, 宫殿, 龙椅, 红墙, 宫廷, 权谋]
  emotion: 威严 / 权力 / 等级
  action_density: low
  shot_kind: first_frame
  prompt: "龙椅、金柱、红墙、玉阶、香炉、屏风；烛光+天窗散射+金饰反光，色彩朱红/金"
  notes: 宫廷/权谋/历史题材；配工笔重彩或写实

- id: `scenes/dungeon`
  category: scenes
  title: 密室/地牢
  tags: [场景, 密室, 地牢, 囚禁, 幽闭, dungeon]
  emotion: 幽闭 / 恐惧 / 压迫
  action_density: low
  shot_kind: both
  prompt: "石墙、铁链、小窗透光、水洼、刑具；单一方向光+长阴影，色彩灰/铁青"
  notes: 悬疑/囚禁/审讯场景；配低键光与湿冷反光

- id: `scenes/inn-tavern`
  category: scenes
  title: 客栈/酒馆
  tags: [场景, 客栈, 酒馆, 江湖, 烟火, 灯笼]
  emotion: 江湖 / 烟火 / 信息交汇
  action_density: medium
  shot_kind: both
  prompt: "木桌木椅、酒坛、灯笼、壁炉、楼梯；烛光+壁炉暖光+窗外月光，色彩暖棕/灯笼红"
  notes: 武侠/冒险/对话场景；配暖调与烟雾氛围

- id: `scenes/temple`
  category: scenes
  title: 寺庙/道观
  tags: [场景, 寺庙, 道观, 佛像, 香火, 禅意]
  emotion: 庄严 / 禅意 / 平静
  action_density: low
  shot_kind: first_frame
  prompt: "大雄宝殿、佛像、香火、钟鼓楼、放生池；香火微光+天光散射+佛像金，色彩木色/金/香火橙"
  notes: 仙侠/禅宗/祈福场景；配体积光与慢镜

- id: `scenes/lab`
  category: scenes
  title: 实验室/研究设施
  tags: [场景, 实验室, 全息屏, 培养舱, 冷峻, lab]
  emotion: 科幻 / 理性 / 冷峻
  action_density: low
  shot_kind: both
  prompt: "玻璃器皿、全息屏幕、机械臂、培养舱；冷白荧光+屏幕蓝光，色彩白/蓝"
  notes: 科幻/AI/生化题材；配冷光与电子音效

- id: `scenes/spaceship`
  category: scenes
  title: 飞船内部
  tags: [场景, 飞船, 太空, 控制台, 舷窗, spaceship]
  emotion: 科幻 / 孤独 / 探索
  action_density: low
  shot_kind: first_frame
  prompt: "金属走廊、舷窗外太空、控制台、休眠舱；LED冷白+舷窗星光，色彩金属灰/星黑"
  notes: 太空/科幻题材；配漂浮粒子与微重力感

- id: `scenes/castle`
  category: scenes
  title: 城堡/要塞
  tags: [场景, 城堡, 要塞, 中世纪, 吊桥, castle]
  emotion: 中世纪 / 权力 / 防御
  action_density: medium
  shot_kind: both
  prompt: "石墙、护城河、吊桥、塔楼、大厅；火把+彩色玻璃+月光，色彩石色/火把橙"
  notes: 中世纪/奇幻/战争题材；配低角度仰拍

## 异世界/超现实 (Fantasy / Surreal)

- id: `scenes/floating-islands`
  category: scenes
  title: 悬浮群岛
  tags: [场景, 悬浮岛, 浮空, 奇幻, 彩虹桥, 仙境]
  emotion: 奇幻 / 仙侠 / 史诗
  action_density: low
  shot_kind: first_frame
  prompt: "浮空岛屿、瀑布坠入虚空、彩虹桥连接；天光通透、云海翻涌，色彩青/白/彩虹色"
  notes: 奇幻/仙侠/异世界场景；配航拍穿越与体积光

- id: `scenes/dreamspace`
  category: scenes
  title: 梦境空间
  tags: [场景, 梦境, 超现实, 不合理建筑, 漂浮, dream]
  emotion: 心理 / 超现实 / 悬疑
  action_density: medium
  shot_kind: both
  prompt: "不合理建筑、漂浮物体、颜色异常；失焦、漂浮、无重力感，色彩梦幻/超现实"
  notes: 心理/超现实/悬疑/回忆场景；配失焦与缓慢漂移

- id: `scenes/data-space`
  category: scenes
  title: 数据空间
  tags: [场景, 数据空间, 代码雨, 虚拟, 光流, cyberspace]
  emotion: 科幻 / AI / 赛博
  action_density: medium
  shot_kind: both
  prompt: "代码雨、几何体、虚拟建筑、光流；数码青绿光、矩阵排列，色彩青/绿/黑"
  notes: 科幻/AI/赛博/脑机接口场景；配数据粒子与全息元素
