# Dialogue — 口吻范式

检索用途：写 `characters[].speech_style` 时参考。**不是成品台词**，禁止整段照抄进 lines[]。
每人一条范式，同场角色口吻必须可区分。

- id: `dialogue/short-staccato`
  category: dialogue
  title: 短句顿挫
  tags: [口吻, 短句, 语速快, 对白]
  medium: film
  genre: thriller
  emotion: 紧张 / 决断
  action_density: medium
  shot_kind: both
  prompt: "每句不超过十字，句号硬切，少形容词，指令多于解释"
  notes: 冷硬主角；不要写成大段独白

- id: `dialogue/slow-drawl`
  category: dialogue
  title: 拖长尾音
  tags: [口吻, 慢, 尾音, 冷笑]
  medium: film
  emotion: 嘲讽 / 从容
  action_density: low
  shot_kind: both
  prompt: "句尾拖长，爱用反问，中间夹一声轻笑，不正面回答"
  notes: 对手/反派；与 short-staccato 对打时节奏一快一慢

- id: `dialogue/rhetorical-cut`
  category: dialogue
  title: 反问切断
  tags: [口吻, 反问, 打断, 对白]
  medium: film
  emotion: 压迫 / 质问
  action_density: medium
  shot_kind: both
  prompt: "先反问再给半句事实，对方未答完就切下一句"
  notes: 审讯/对峙；潜台词靠打断，不靠心理独白

- id: `dialogue/technical-plain`
  category: dialogue
  title: 讲解平述
  tags: [口吻, 讲解, 口播, 科普]
  medium: spoken
  emotion: 清晰 / 中性
  action_density: low
  shot_kind: both
  prompt: "一词一义，先结论后例子，少口语填充（那个/就是），数字说全"
  notes: 口播/产品；不要文艺比喻压过信息

- id: `dialogue/understate`
  category: dialogue
  title: 轻描重压
  tags: [口吻, 克制, 轻描, 对白]
  medium: film,documentary
  emotion: 克制 / 余韵
  action_density: low
  shot_kind: both
  prompt: "大事用小词，不升调，关键信息放句末，前后留停顿"
  notes: 纪实对白或压抑家庭戏；禁止喊叫宣泄

- id: `dialogue/silence-beat`
  category: dialogue
  title: 先静后切
  tags: [口吻, 停顿, 沉默, 节拍]
  medium: film
  emotion: 紧张 / 犹豫
  action_density: low
  shot_kind: video
  prompt: "开口前可见换气或舔唇，说半句停下，用动作补完（放下杯/转头）"
  notes: 不是「无对白」；沉默必须有可见动作节拍
