# Actions — 动作 / 姿态 / 表情

检索用途：在 `asset-director` 2b 产出 `subjects[].action` / `action_sequence`（量化动作节拍）
与 `emotion`（面部神情）时参考。词条强调**量化**（速度/幅度/步数/接触点/身体部位），
禁止抽象情绪词（"优雅地/自然地"必须替换为可执行描述）。

## 动作节拍 (Action Sequence)

- id: `actions/beat-walk`
  category: actions
  title: 行走/奔跑量化
  tags: [动作, 行走, 奔跑, 走, 跑, walk, run]
  emotion: 中性 / 决心 / 急切
  action_density: medium
  shot_kind: video
  prompt: "行走：慢走/快走/疾走/奔跑；每步约0.5秒、大步/小步、重心前倾、双臂摆动幅度、鞋跟先着地；右手扶门/触碰墙面等接触点"
  notes: 必须量化速度+幅度+步数+接触点；例：'每步约0.5秒、大步、重心前倾、右手扶门' 而非 '自然地向门走去'

- id: `actions/beat-object-contact`
  category: actions
  title: 物体接触动作
  tags: [动作, 接触, 物体, 拿起, 放下, 触碰, contact]
  emotion: 中性 / 紧张
  action_density: medium
  shot_kind: video
  prompt: "双手捧起马克杯，低头抿了一口，随后轻轻放回桌面，杯底与木桌发出轻响；用手背擦去眼角泪痕，随后转身快步走向门口"
  notes: 写清哪只手接触哪个道具/地面，以及接触产生的效果；接触点是 SFX 音效锚定的依据

- id: `actions/beat-entrance`
  category: actions
  title: 入场动作
  tags: [动作, 入场, 入画, entrance, 亮相]
  emotion: 登场 / 气势 / 紧张
  action_density: medium
  shot_kind: video
  prompt: "从画面左侧快速入画，目光锁定目标，建立对峙；或缓步入画，人物占据画面对角线，形成亮相"
  notes: 入场要写清方向、速度、视线；可配低角度仰拍增强气势

- id: `actions/beat-combat`
  category: actions
  title: 打斗动作序列
  tags: [动作, 打斗, 战斗, 动作戏, combat, fight, wuxia]
  emotion: 紧张 / 高燃 / 爆发
  action_density: high
  shot_kind: video
  medium: film,anime
  genre: action,wuxia
  prompt: "动作序列：①右手抽枪对准目标开火（子弹时间、枪口闪光、慢动作）②收枪→右手蓄能→直拳打击对方头部将其击飞 ③转身→肘击 ④站立收尾，敌人倒地，逆光剪影"
  notes: 不写'打架'；江湖/比武才用本条。日常冲突改用 beat-grab-collar / beat-shove-wall

- id: `actions/beat-grab-collar`
  category: actions
  title: 抓领口
  tags: [动作, 抓, 领口, 接触, 冲突]
  emotion: 愤怒 / 压迫
  action_density: high
  shot_kind: video
  medium: film
  genre: thriller,action
  prompt: "右手五指抓住对方衣领向上提，前臂贴胸，两人鼻尖接近，被抓一方后脚跟离地"
  notes: 「两人打架」的合格改写之一；写清哪只手、接触点=衣领

- id: `actions/beat-shove-wall`
  category: actions
  title: 推向墙
  tags: [动作, 推, 墙, 接触, 冲突]
  emotion: 爆发 / 压制
  action_density: high
  shot_kind: video
  medium: film
  genre: thriller,action
  prompt: "双手按住对方双肩推向墙面，后背撞墙，后脑碰到墙面，粉尘落下"
  notes: 接触点=肩/墙；可接抓领口

- id: `actions/beat-dodge`
  category: actions
  title: 侧闪躲让
  tags: [动作, 闪, 躲, 接触, 防御]
  emotion: 紧张 / 机敏
  action_density: high
  shot_kind: video
  medium: film,anime
  genre: action
  prompt: "重心下沉，左肩后撤，来拳擦过右颊，衣领被带起一角，随即侧步站稳"
  notes: 接触点可以是擦过的空气+衣领；不要只写「躲开了」

- id: `actions/beat-chase`
  category: actions
  title: 追逐动作
  tags: [动作, 追逐, 奔跑, 逃, 追, chase]
  emotion: 紧张 / 急促 / 逃跑
  action_density: high
  shot_kind: video
  prompt: "全力奔跑：身体前倾、双臂大摆、鞋跟重重落地，呼吸急促；穿越障碍时侧身翻滚快速起身，警惕回望，继续冲刺"
  notes: 追逐配手持/跟拍运镜；写清方向、速度变化、障碍物接触

- id: `actions/beat-fall`
  category: actions
  title: 受伤/踉跄/倒地
  tags: [动作, 受伤, 踉跄, 倒地, 跪地, fall, injured]
  emotion: 疲惫 / 力竭 / 绝望
  action_density: medium
  shot_kind: video
  prompt: "受伤踉跄：手捂伤口、踉跄后退、咬牙；力竭跪地：双膝跪地、双手撑地、低头喘息；肩膀下垂、身体微弯、双手撑膝"
  notes: 战后/末日/受伤场景；动作由急促逐渐转为瘫软，配合喘息

## 姿态 (Body Language)

- id: `actions/posture-defensive`
  category: actions
  title: 防御交叉站姿
  tags: [姿态, 防御, 交叉, 站姿, 对峙, posture]
  emotion: 防御 / 不信任 / 封闭
  action_density: low
  shot_kind: first_frame
  prompt: "双臂交叉胸前、微微后仰、身体重心后移；与对手保持约3步距离，对峙状态"
  notes: 对峙/猜疑场景；配戒备眼神

- id: `actions/posture-aggressive`
  category: actions
  title: 攻击前倾姿态
  tags: [姿态, 攻击, 前倾, 握拳, 战斗]
  emotion: 攻击 / 威胁 / 对峙
  action_density: high
  shot_kind: both
  prompt: "身体前倾、拳头紧握（指节发白）、目光锁定目标；双腿分开微蹲、重心降低、握武器呈战斗准备姿势"
  notes: 战斗/冲突/对峙；可配低角度仰拍强化压迫

- id: `actions/posture-anxious`
  category: actions
  title: 焦虑/紧张姿态
  tags: [姿态, 焦虑, 紧张, 踱步, 搓手, 不安]
  emotion: 焦虑 / 等待 / 不安
  action_density: medium
  shot_kind: video
  prompt: "来回踱步、不时看表、手指敲击桌面；或双手搓揉、手指交叉、咬唇；目光闪躲、眉头压低"
  notes: 悬疑/限时/等待场景；配轻微手持增强不安

- id: `actions/posture-grieving`
  category: actions
  title: 悲伤/崩溃姿态
  tags: [姿态, 悲伤, 崩溃, 掩面, 哭泣, 绝望]
  emotion: 悲伤 / 崩溃 / 绝望
  action_density: low
  shot_kind: both
  prompt: "双手掩面、手指穿插头发、肩膀颤抖；或扶额、闭眼、头微垂；眼眶泛红、泪水滑落"
  notes: 悲剧/虐心场景；表情写面部（眼眶泛红/嘴角抽搐），不写'伤心'

## 表情 (Expression / Micro-expression)

- id: `actions/expr-restrained`
  category: actions
  title: 隐忍表情
  tags: [表情, 隐忍, 克制, 咬牙, 抿唇, expression]
  emotion: 隐忍 / 压抑
  action_density: low
  shot_kind: first_frame
  prompt: "眉头压低、嘴唇紧抿、牙关咬紧、下颌线紧绷，眼神克制但瞳孔微缩"
  notes: 压抑情绪场景；面部描写是情绪锚点，特写时用

- id: `actions/expr-surprise`
  category: actions
  title: 震惊/惊恐表情
  tags: [表情, 震惊, 惊恐, 瞳孔, 瞪眼, surprise]
  emotion: 震惊 / 惊恐 / 惊觉
  action_density: medium
  shot_kind: both
  prompt: "瞳孔骤然放大、双眉上挑、嘴唇微张、身体下意识后倾，呼吸屏住"
  notes: 真相揭露/惊悚遭遇；配大特写与轻微推近增强冲击

- id: `actions/expr-contempt`
  category: actions
  title: 傲慢/不屑表情
  tags: [表情, 傲慢, 不屑, 冷笑, 俯视, contempt]
  emotion: 傲慢 / 不屑
  action_density: low
  shot_kind: first_frame
  prompt: "下巴抬起、嘴角一侧上挑成冷笑、眼神居高临下俯视对方，眼皮半垂"
  notes: 身份对立/权力场景；配低角度仰拍

- id: `actions/expr-tears`
  category: actions
  title: 含泪/哭泣表情
  tags: [表情, 含泪, 哭泣, 眼泪, 泪痕, tears]
  emotion: 悲伤 / 不舍 / 感动
  action_density: low
  shot_kind: first_frame
  prompt: "眼眶泛红、泪水在眼眶打转/滑落脸颊留下泪痕、鼻尖泛红、嘴唇微微颤抖，强忍或无声哭泣"
  notes: 离别/治愈/感动场景；特写时泪水反光增加感染力
