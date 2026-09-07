# Styles — 风格预置

检索用途：在 `scene-director` / `asset-director` 产出镜头时，根据题材/世界观匹配风格词条，
作为"风格核心"参考（选 3-5 个核心 + 2-3 个去 AI 味词 + 3-5 个限制词）。风格词条是
**视觉语言锚点**，用于保证全片视觉一致，但 LLM 需改编而非整段照抄（防过度统一）。

## 语言说明（2026-09 起全中文政策，用户写死）

- 本 INDEX 及 `prompt_library/` 全部词条**保持中文**，LLM 改编后产出中文
  `visual_details` 字段，进入 `video_prompt` / `first_frame_prompt` 中文核心段。
- **prompt 与 notes 正文一律纯中文，禁止出现英文锚点/英文风格词/英文负向词**；
  必要的专业术语用「中文（白话短注）」表达。工程标识符例外：`tags` 行英文检索词、
  `genre` 族名、playbook/词条 id、许可证与来源仓库名——它们不注入最终提示词。
- 英文画质层已废除：`english_visual=True` 时追加的【画质基底】/【衔接】已中文化
  （见 `lib/prompt_phrases.py`），`negative_prompt` 亦为中文
  （`default_english_negative_prompt()` 返回值，函数名保留兼容）。
- 详见 `skills/creative/prompt-library-usage.md` §词条语言与英文画质层。

## 中文/东方风格 (Chinese / Eastern)

- id: `styles/chinese-ink-wash`
  category: styles
  title: 水墨写意
  tags: [风格, 水墨, 写意, 中国风, 古风, ink-wash]
  emotion: 诗意 / 禅意 / 留白
  action_density: low
  shot_kind: first_frame
  prompt: "水墨写意：墨色浓淡变化、大量留白、毛笔笔触、宣纸质感；烟雨/山水/竹林，意境高于写实"
  notes: 中国风/诗词意境/文化题材；配 chinese-elegance playbook；留白是核心，勿堆满

- id: `styles/chinese-gongbi`
  category: styles
  title: 工笔重彩
  tags: [风格, 工笔, 重彩, 国风, 青绿, gongbi]
  emotion: 华美 / 典雅 / 富贵
  action_density: medium
  shot_kind: first_frame
  prompt: "工笔重彩：精细线描、青绿/朱红/黛青重彩、绢本质感、宫廷/仕女/花鸟题材"
  notes: 古典华美/宫闱/民俗；色彩浓而不艳，线条清晰

- id: `styles/chinese-wuxia`
  category: styles
  title: 武侠/仙侠
  tags: [风格, 武侠, 仙侠, 剑, 古风, 飞檐走壁]
  emotion: 侠气 / 飘逸 / 凌厉
  action_density: high
  shot_kind: both
  prompt: "武侠/仙侠：烟雨、竹林、剑光、白衣、留白、飞檐走壁；衣袂翻飞、剑气纵横，动作飘逸凌厉"
  notes: 打斗/江湖/修仙；动作戏配战斗动作序列词条与低角度仰拍

- id: `styles/chinese-horror`
  category: styles
  title: 中式惊悚
  tags: [风格, 惊悚, 恐怖, 中式, 民俗, horror]
  emotion: 不安 / 压抑 / 诡异
  action_density: medium
  shot_kind: both
  prompt: "中式惊悚：青灰冷调、红灯笼/纸人/剪纸/祠堂等民俗元素、窄巷深宅、雾气弥漫、暗部隐藏威胁"
  notes: 中式恐怖/悬疑；配低键光、荷兰角、空镜余韵；避免欧美血浆风

- id: `styles/chinese-republican`
  category: styles
  title: 民国/复古港风
  tags: [风格, 民国, 复古港风, 老上海, 怀旧, retro]
  emotion: 怀旧 / 复古 / 年代感
  action_density: medium
  shot_kind: both
  prompt: "民国/复古港风：黄绿高光、复古暖橙、胶片噪点、茶餐厅/老街道/霓虹招牌/留声机，80年代磁带质感"
  notes: 年代剧/怀旧/老上海；配复古胶片设备与暖橙色调

## 动漫/插画风格 (Anime / Illustration)

- id: `styles/anime-ghibli`
  category: styles
  title: 吉卜力/宫崎骏
  tags: [风格, 吉卜力, 宫崎骏, 动漫, 治愈, ghibli]
  emotion: 温馨 / 治愈 / 奇幻
  action_density: medium
  shot_kind: both
  prompt: "吉卜力风：手绘质感、柔和的自然光、丰富绿植与自然元素、云朵棉花质感、温暖治愈的氛围；手绘粒子、绘画感光线"
  notes: 治愈/自然/奇幻题材；配 ghibli-style playbook；色调柔和偏暖，线条手绘感

- id: `styles/anime-cel`
  category: styles
  genre: animation-film
  title: 日系赛璐璐/动画
  tags: [风格, 日漫, 赛璐璐, 动画, anime, cel]
  emotion: 活泼 / 热血 / 日常
  action_density: high
  shot_kind: both
  medium: anime
  prompt: "赛璐璐平涂、清晰闭合黑线、有限阴影分层、高饱和色块；硬边不是写实皮肤"
  notes: 技法锚点，不具名作者；动作戏可加速度线，表情节制或夸张由 playbook 定；genre 归 animation-film 族（2026-09 补）

- id: `styles/anime-sci-fi`
  category: styles
  title: 机甲/科幻动画
  tags: [风格, 机甲, 科幻, 机械, 赛博格, mecha]
  emotion: 热血 / 燃 / 科技
  action_density: high
  shot_kind: both
  prompt: "机甲/科幻动画：机甲、机械外骨骼、HUD界面、等离子武器、过载警报、动力靴喷射、赛博格"
  notes: 机甲战/科幻动作；配能量电弧特效词条与镜头光晕

## 电影/写实风格 (Cinematic / Realistic)

- id: `styles/cinematic-film`
  category: styles
  genre: photoreal
  title: 电影质感
  tags: [风格, 电影感, 电影质感, 胶片, cinematic]
  emotion: 史诗 / 高级 / 沉浸
  action_density: medium
  shot_kind: both
  prompt: "电影质感：电影摄影机拍摄、35毫米胶片颗粒、宽银幕画幅、浅景深、高对比、镜头光晕、动态模糊"
  notes: 通用高级质感；配设备+影调词条；选1个设备1个影调即可，勿堆砌；2026-09 增强：布光可用四段式（光的软硬/方向/色温/光源动机，如暖色主光侧切、轮廓光勾边），实机/镜头/胶片词参考导演台本预设方法论；genre 归 photoreal 族

- id: `styles/cinematic-noir`
  category: styles
  genre: photoreal
  title: 黑色电影/悬疑
  tags: [风格, 黑色电影, 悬疑, 冷调, noir]
  emotion: 悬疑 / 压抑 / 神秘
  action_density: medium
  shot_kind: both
  prompt: "黑色电影：黑白或低饱和冷调、深阴影、硬光、烟雾弥漫、雨夜、窥视视角、荷兰角"
  notes: 悬疑/犯罪/心理；配低键光与雨/烟雾特效；genre 归 photoreal 族（2026-09 补）

- id: `styles/cinematic-postapoc`
  category: styles
  title: 末日废土
  tags: [风格, 末日, 废土, 丧尸, 废墟, postapoc]
  emotion: 绝望 / 荒凉 / 生存
  action_density: high
  shot_kind: both
  prompt: "末日废土：灰黄调、锈蚀金属、破败城市、辐射云、沙尘暴、临时营地、生锈汽车；废墟纪实风格"
  notes: 末日/战争/丧尸题材；配废墟纪实关键词与冷黄调；动作戏加限制词

- id: `styles/cinematic-cyberpunk`
  category: styles
  genre: techno-punk
  title: 赛博朋克
  tags: [风格, 赛博朋克, 霓虹, 义体, 未来, cyberpunk]
  emotion: 冷峻 / 都市 / 未来
  action_density: high
  shot_kind: both
  prompt: "赛博朋克：霓虹（品红+青蓝）、雨夜、义体、机械义肢、LED招牌、全息投影、东京都心/九龙城寨窄巷"
  notes: 科幻都市/黑客/义体题材；配霓虹补光、雨夜、数据粒子；2026-09 增强：场景氛围可用美学期模板词改写（浓密霓虹都市氛围、湿漉漉反光地面、品红青蓝撞色、老旧高科技材质、深夜浓影、避免干净的企业极简风）；genre 归 techno-punk 族

## 视觉基调/设备 (Visual Basis / Device)

- id: `styles/device-phone-pov`
  category: styles
  title: 手机竖屏/监控/特殊设备视角
  tags: [风格, 手机, 竖屏, 监控, 摄像头, 鱼眼, device]
  emotion: 纪实 / 真实 / 被监视
  action_density: medium
  shot_kind: video
  prompt: "特殊设备视角：手机竖屏拍摄/运动相机广角/监控摄像头/鱼眼镜头/红外热成像/夜视仪绿色画面/针孔摄像头"
  notes: 纪实/偷窥/监视/第一人称惊悚；配对应设备质感与轻微噪点/畸变

## 去 AI 味 / 限制词 (Anti-AI / Limiter)

- id: `styles/anti-ai-realistic`
  category: styles
  title: 去 AI 味核心词
  tags: [风格, 去AI味, 超写实, 逼真, 实拍, 质感]
  emotion: 真实 / 高级
  action_density: medium
  shot_kind: both
  prompt: "超写实、极致逼真、真人实景拍摄、电影动作捕捉、复古胶片质感；建议至少选2个组合使用"
  notes: 减少 CG 感/塑料感；含人物场景必用'真人实景拍摄'；动作戏加'电影动作捕捉'

- id: `styles/limiter-action`
  category: styles
  title: 动作/画面限制词
  tags: [风格, 限制词, 杜绝, 动作僵硬, 畸形, 负面]
  emotion: 中性（负向约束）
  action_density: high
  shot_kind: video
  prompt: "杜绝动作僵硬、杜绝人物畸形手指、杜绝穿模、杜绝肢体扭曲、杜绝人物漂浮、杜绝关节反向、杜绝镜头漂移、杜绝游戏CG感、杜绝光影不一致、杜绝重力失真"
  notes: 选 3-5 个与场景最相关的即可，过多分散注意力；动作戏必加'杜绝动作僵硬、肢体扭曲'

- id: `styles/limiter-quality`
  category: styles
  title: 画质限制词
  tags: [风格, 限制词, 画质, 低分辨率, 水印, 崩坏]
  emotion: 中性（负向约束）
  action_density: low
  shot_kind: both
  prompt: "杜绝低分辨率、杜绝画面崩坏、杜绝水印、杜绝塑料皮肤、杜绝过度磨皮、杜绝镜头抖动过度"
  notes: 提升成片质量；与动作限制词配合，控制总量 3-5 个

---

以下为 2026-09 扩充：五大族树形风格库（genre 字段=族名，检索按 genre 过滤防跨族污染；
叶子词条自包含，整条返回、永不跨条拼词；单项目只锁 1 个叶子写入 bible.style_lock）。
来源与许可见 `prompt_library/CREDITS.md`「W2 风格族库」节。

## 国风传统（chinese-traditional）

- id: `styles/ct-lianhuanhua-liujiyou`
  category: styles
  genre: chinese-traditional
  title: 白描连环画·刘继卣锚点
  tags: [风格, 连环画, 白描, 小人书, 墨线, 刘继卣, lianhuanhua, baimiao]
  emotion: 古朴 / 叙事 / 雄浑
  action_density: medium
  shot_kind: both
  prompt: "老连环画白描：这是中国传统白描连环画（老版小人书）插图，纯黑白墨线线描，近乎纯白宣纸底，墨块浓淡分明，线条细密工整，六七十年代《三国演义》连环画版式与印刷质感；画师锚点：工写结合，线条刚劲有力，衣纹用笔潇洒流畅，人物造型精准生动，神态刻画入微，画面气势雄浑；正向禁令：无彩色上色、无厚涂油画、无3D渲染、无动漫、无照片写实、无渐变柔光、无数码感；白描水墨画笔技法、传统连环画画种、精细墨线勾勒、衣发处大块墨色填黑、强烈黑白对比"
  notes: 来源 LianHuanAI(MIT) 画师风格库改写；风格头必须前置；同片多角色换画师锚点（如胡若佛/凌涛）避同质化；探针=甲轨王生定妆

- id: `styles/ct-lianhuanhua-huruofu`
  category: styles
  genre: chinese-traditional
  title: 白描连环画·胡若佛锚点
  tags: [风格, 连环画, 白描, 仕女, 行云流水, 胡若佛, lianhuanhua, baimiao]
  emotion: 古朴 / 婀娜 / 精美
  action_density: low
  shot_kind: both
  prompt: "老连环画白描：这是中国传统白描连环画（老版小人书）插图，纯黑白墨线线描，近乎纯白宣纸底，墨块浓淡分明，线条细密工整，六七十年代连环画印刷质感；画师锚点：线条精细至极，衣纹如行云流水，人物婀娜多姿，画面精美绝伦，仕女与美人态尤为出众；正向禁令：无彩色上色、无厚涂油画、无3D渲染、无动漫、无照片写实、无渐变柔光、无数码感；白描水墨画笔技法、传统连环画画种、行云流水般的飘逸墨线、强烈黑白对比"
  notes: 来源 LianHuanAI(MIT) 画师风格库改写；适合女性角色/美人态锚点；与刘继卣条目同片分角色使用；探针=甲轨备选

- id: `styles/ct-cinnabar-woodcut`
  category: styles
  genre: chinese-traditional
  title: 朱砂木刻版画
  tags: [风格, 木刻, 版画, 朱砂, 红色线描, 古籍插图, woodcut, vermilion]
  emotion: 古拙 / 印刷感 / 肃穆
  action_density: medium
  shot_kind: both
  prompt: "朱砂木刻：以朱砂红单色线描为主的东方木刻版画，线条带手工雕版断续与毛边，似雕版印刷渗墨的颗粒感，米白/宣纸/旧纸底，古籍插图与民间纸印质感，构图古典、人物线条简练；正向禁令：无多色套色、无照片写实、无3D渲染、无现代数字感、无渐变；朱砂红木刻版画、红单色线描、中国传统雕版印刷、宣纸颗粒纹理、古籍插图风格"
  notes: 参考 promptsref 朱砂木刻 sref 拆解（方法论参考非照抄）；单色红+纸纹，与白描双轨对比用；探针=甲轨

- id: `styles/ct-gongbi-baimiao`
  category: styles
  genre: chinese-traditional
  title: 工笔白描（素雅淡彩）
  tags: [风格, 工笔, 白描, 游丝描, 淡彩, 素雅, gongbi, line-drawing]
  emotion: 素雅 / 工整 / 静气
  action_density: low
  shot_kind: both
  prompt: "工笔白描：游丝描/铁线描细匀线条勾勒，线条细劲连贯、粗细如一，淡墨分染衣纹层次，素雅设色或不设色，绢本/宣纸质感，构图工整留白；与工笔重彩不同，此条目去重彩、以线为主；正向禁令：无厚涂、无油画笔触、无3D渲染、无照片写实、无浓艳重彩；精细工笔画线条、一丝不苟的墨线勾勒、绢本上的淡雅晕染、中国传统线描人物"
  notes: 与既有 styles/chinese-gongbi（重彩）互斥选用：要线选本条，要色选彼条；探针=甲轨

## 动画电影（animation-film）

- id: `styles/af-pixar-3d`
  category: styles
  genre: animation-film
  title: 好莱坞式3D动画电影
  tags: [风格, 3D动画, 动画电影, 皮克斯风, pixar-inspired, 3d-render]
  emotion: 温暖 / 立体 / 电影感
  action_density: medium
  shot_kind: both
  prompt: "好莱坞式三维动画电影质感（三维立体渲染动画，非真实拍摄，不提及品牌名）：角色眼睛大而传神并带多层眼神光，皮肤有次表面散射（光线透进皮肤的柔软通透感），主光加轮廓光加接触阴影三层布光，浅景深背景奶油般虚化，材质细节丰富（布纹、釉面、根根分明的头发丝），整体温和胶片调色；角色设计须与知名动画形象明显区分；负向：照片写实、真人实拍、二维赛璐璐平涂、扁平插画、多出手指、面部扭曲"
  notes: 来源 krusemediallc/arcads + gptimager Pixar 公式改写；失败模式=只写"Pixar style"被默认填充，必须写全光效/材质/景深；探针=乙轨壁纸

- id: `styles/af-ghibli-plus`
  category: styles
  genre: animation-film
  title: 手绘水彩治愈动画（吉卜力增强）
  tags: [风格, 吉卜力, 水彩, 手绘, 治愈, ghibli-style, watercolor-anime]
  emotion: 温馨 / 呼吸感 / 怀旧
  action_density: low
  shot_kind: both
  prompt: "吉卜力式手绘水彩动画插画：手绘水彩质感，单一清晰焦点主体，背景简化成大色块平滑渐变，云少而整（几朵大而完整的软云，忌碎云堆砌），大面积安静留白，柔和赛璐璐分层上色，自上而下暖光漫射带柔光弥漫感，色彩明亮和谐低对比；负向：琐碎堆砌的过度细节构图、斑驳杂点的纹理、三维渲染、照片写实、数码矢量感"
  notes: 来源 hand-drawn-styles 实测增强版（反堆细节——lush/detailed 会出稀碎感，已剔除）；增强既有 styles/anime-ghibli；探针=乙轨壁纸

- id: `styles/af-shinkai-light`
  category: styles
  genre: animation-film
  title: 日式唯美光效动画
  tags: [风格, 唯美, 光效, 逆光, 光斑, 新海诚式, shinkai-style, lens-flare]
  emotion: 唯美 / 惆怅 / 闪耀
  action_density: low
  shot_kind: both
  prompt: "日式唯美光效动画：极致的天空与云层刻画，饱和蓝天/晚霞渐变，强烈逆光与体积光（光束中可见光尘），镜头光斑与漏光光晕，雨后湿润反光地面，细腻色彩渐变与高光闪烁，人物发丝边缘透光；构图唯美有纵深感；负向：呆板的平光、浑浊发灰的色彩、照片写实毛孔、粗重颗粒"
  notes: 以视觉语言命名（在世作者具名仅入 tags 检索词，同 anime-ghibli 先例）；强逆光+高饱和是身份词；探针=乙轨壁纸

## 漫画插画（comics-illustration）

- id: `styles/ci-american-comic`
  category: styles
  genre: comics-illustration
  title: 美式漫画硬线
  tags: [风格, 美漫, 硬线, 四色印刷, 网点, american-comic, bold-ink]
  emotion: 力量感 / 张扬 / 戏剧性
  action_density: high
  shot_kind: both
  prompt: "美式漫画硬线风格：粗黑勾线，硬边块面阴影与十字网点排线，高饱和四色印刷感（品红/青/黄/黑轻微套印错位），动感速度线与拟声词留位，肌肉结构与透视夸张有力，纸面印刷质感；负向：柔和喷笔、照片写实、水彩晕染、纤细潦草线条、三维渲染"
  notes: 参考 ComfyUI-Style-Prompts-Collection Cartoon 族改写；与日漫赛璐璐互斥（勾线粗细/阴影方式不同）；探针=乙轨壁纸

- id: `styles/ci-cyberpunk-manga`
  category: styles
  genre: comics-illustration
  title: 赛博朋克漫画（黑白网点/全彩霓虹双模板）
  tags: [风格, 赛博朋克, 漫画, 网点, 霓虹, noir, cyberpunk-manga]
  emotion: 冷峻 / 压迫 / 反乌托邦
  action_density: high
  shot_kind: both
  prompt: "赛博朋克漫画：黑白墨线插画，未来巨型都市的层叠楼体与悬垂线缆，密集广告牌与全息招牌渲染为亮白网点光斑，重度网点纸阴影，高对比黑色电影式硬阴影，精致墨线细节，霓虹只用小面积点缀色（品红/电青）；全彩变体：霓虹漫画风、品红与青蓝双色霓虹辉光、电光蓝高光、大气薄雾、电影级布光、饱满艳丽的色彩；负向：照片写实、柔和粉彩、干净的企业极简风"
  notes: 来源 Gootaku 双模板改写；黑白网点版配 manga_panel playbook，全彩霓虹版配 cyberpunk_neon playbook；探针=乙轨壁纸

- id: `styles/ci-xkcd-lecture`
  category: styles
  genre: comics-illustration
  title: 极简线条讲解漫画
  tags: [风格, 火柴人, 极简, 讲解图, xkcd, stick-figure, diagram]
  emotion: 理性 / 幽默 / 轻松
  action_density: low
  shot_kind: both
  prompt: "极简黑白线条讲解漫画：画在米白/奶油色纸上；硬性负向约束（必须前置）：严禁实心黑色填充、严禁剪影、严禁排线与阴影、严禁明暗体积感、严禁厚涂渲染，人物与物体一律只用细线描边；圆圈头+极简点线五官火柴人，细均匀略手抖黑色钢笔线，纯轮廓线稿，平面二维无透视，概念化示意图式讲解感"
  notes: 来源 hand-drawn-styles xkcd 配方；负向约束前置是实测命门（否则渲染成厚黑块）；配 spoken_explain playbook；探针=乙轨壁纸

## 真人写实（photoreal）

- id: `styles/photoreal-cinematic-enhanced`
  category: styles
  genre: photoreal
  title: 电影质感·增强锚点（配合 styles/cinematic-film 使用）
  tags: [风格, 电影质感, 布光, 实机, 胶片, cinematic, lens]
  emotion: 高级 / 真实 / 沉浸
  action_density: medium
  shot_kind: both
  prompt: "电影质感增强锚点（配合电影质感基础词条使用）：布光四段式（光的软硬/方向/色温/光源动机，如三千二百开尔文暖色主光从侧上方斜切、面部轮廓带边缘光、环境沉入阴影）；摄影机、镜头与胶片锚点（如数字电影机、球面镜头、五百开胶片色温质感）；浅景深奶油般虚化、胶片颗粒、高动态范围；只与真人写实族组合，勿与其他画风词条混用"
  notes: 来源 DirectorsConsole/madebysaira/LzyPrompt 方法论改写；本条是 cinematography 词不是画风词，仅与 photoreal 族组合；探针=乙轨壁纸（电影质感）

- id: `styles/photoreal-noir-enhanced`
  category: styles
  genre: photoreal
  title: 黑色电影·增强锚点（配合 styles/cinematic-noir 使用）
  tags: [风格, 黑色电影, 低键光, 雨夜, 烟雾, noir, low-key]
  emotion: 悬疑 / 压抑 / 神秘
  action_density: medium
  shot_kind: both
  prompt: "黑色电影增强锚点（配合黑色电影基础词条使用）：低键布光，单一硬光源，硬边阴影占画面一半以上，百叶窗/雨/烟雾形成的体积光束，湿漉漉街面反射霓虹或车灯，深黑占主导的低饱和冷调，高对比宽容度；只与真人写实族组合，勿与其他画风词条混用"
  notes: 来源 LzyPrompt/DirectorsConsole 布光方法论改写；cinematography 词，仅与 photoreal 族组合；探针=乙轨壁纸（黑色电影）

## 科技朋克（techno-punk）

- id: `styles/tf-neon-lineart`
  category: styles
  genre: techno-punk
  title: 霓虹线稿
  tags: [风格, 霓虹, 线稿, 发光, 深底, neon-lineart, glow]
  emotion: 迷幻 / 未来 / 冷光
  action_density: low
  shot_kind: both
  prompt: "霓虹线稿：深色（近黑/深蓝紫）背景上以发光单色/双色霓虹管线条勾勒全部形体，线条即光源，青+品红双色为主，辉光自然溢出，微弱环境反射，主体轮廓外几乎无细节，极简未来感；负向：全彩渲染、柔和明暗过渡、照片写实、杂乱背景"
  notes: 参考 ComfyUI-Style-Prompts-Collection Neon Line Art 改写；与赛博朋克（全场景渲染）互斥——本条只有线发光；探针=乙轨壁纸

- id: `styles/tf-mech-lowpoly`
  category: styles
  genre: techno-punk
  title: 低多边形机甲/硬表面
  tags: [风格, 低多边形, 机甲, 硬表面, low-poly, mech, hard-surface]
  emotion: 硬朗 / 游戏 / 几何
  action_density: high
  shot_kind: both
  prompt: "低多边形三维渲染：低多边形渲染、无纹理渐变的平涂分面、清晰的多面体硬边色面、简洁几何体块构成、硬边光照与锐利高光、少量纯色块面（二到四色）、游戏美术资产质感；机甲/载具/硬表面主题尤佳；负向：平滑有机曲线、照片级纹理、皮肤次表面散射、手绘笔触"
  notes: 参考 ComfyUI-Style-Prompts-Collection Low Poly/Mech/Game Art 族改写；游戏宣传片/科幻短片向；探针=乙轨壁纸
