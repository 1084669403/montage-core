# SFX — 音效索引（Sonniss GDC Game Audio Bundle）

> 来源：配方槽对照 [Sonniss GDC Game Audio Bundle](https://sonniss.com/gameaudiogdc/)；
> 已钉 `source_url` 的是已 HEAD 验证的 **CC0 / CC-BY** 替代文件（Wikimedia / BigSoundBank），
> 不是 Sonniss 整包直链。`file:` 扩展名与载荷一致。无直链时
> `python assets/scripts/fetch_assets.py --sfx`。SA/NC 不钉。
>
> 使用：`asset_retriever` 按情绪/场景检索；剪辑时与镜头动作节拍对齐放置。

## 环境音 (Ambience)

- id: `sfx/rain-heavy`
  category: sfx
  title: 暴雨+雷鸣（环境铺底）
  tags: [雨, 暴雨, 雷, 环境音, rain, storm, 雨夜]
  emotion: 压抑 / 紧张
  mood: 雨夜
  bpm: 0
  duration_seconds: 10
  license: CC0
  source: Wikimedia Commons（File:Rain.ogg，公有领域；约 10s，短氛围）
  file: sfx/rain-heavy.ogg
  source_url: https://commons.wikimedia.org/wiki/Special:FilePath/Rain.ogg
  attribution: ""
  notes: 雨夜底层氛围；短文件不循环拉长，混音裁到镜头时长。低于对白 12-18dB

- id: `sfx/rain-light-drizzle`
  category: sfx
  title: 细雨（轻声环境）
  tags: [雨, 细雨, 环境音, drizzle, 治愈]
  emotion: 安静 / 惆怅
  mood: 雨天
  bpm: 0
  duration_seconds: 60
  license: CC0
  source: Sonniss GDC（Rain 类）
  file: sfx/rain-light-drizzle.wav
  source_url: ""
  attribution: ""
  notes: 回忆/离别段落；低电平循环，避免雨声盖过对白

- id: `sfx/birds-dawn`
  category: sfx
  title: 清晨鸟鸣
  tags: [鸟, 清晨, 黎明, 环境音, birds, dawn]
  emotion: 宁静 / 新生
  mood: 清晨
  bpm: 0
  duration_seconds: 30
  license: CC0
  source: Sonniss GDC（Nature/Birds 类）
  file: sfx/birds-dawn.wav
  source_url: ""
  attribution: ""
  notes: 黎明段落/劫后余生的转场；与 golden-hour 光线镜头搭配

- id: `sfx/room-tone-quiet`
  category: sfx
  title: 安静房间底噪
  tags: [底噪, 房间, 环境音, room-tone, 静场]
  emotion: 空寂 / 悬停
  mood: 静场
  bpm: 0
  duration_seconds: 30
  license: CC0
  source: Sonniss GDC（Room Tone 类）
  file: sfx/room-tone-quiet.wav
  source_url: ""
  attribution: ""
  notes: 静场段落必铺（否则 AI 视频静音感突兀）；所有对白段落的底层

- id: `sfx/crowd-murmur`
  category: sfx
  title: 人群嘈杂
  tags: [人群, 嘈杂, 环境音, crowd, 街头]
  emotion: 烟火 / 焦躁
  mood: 公共空间
  bpm: 0
  duration_seconds: 30
  license: CC0
  source: Sonniss GDC（Crowd 类）
  file: sfx/crowd-murmur.wav
  source_url: ""
  attribution: ""
  notes: 开场建世（茶馆/车站类镜头）与群像段落；可低通滤波模拟隔墙听感

## 动作与冲击 (Action & Impact)

- id: `sfx/footsteps-wood`
  category: sfx
  title: 木质脚步声
  tags: [脚步, 木质, 地板, footsteps, 逼近]
  emotion: 压迫 / 逼近
  mood: 紧张
  bpm: 0
  duration_seconds: 10
  license: CC0
  source: BigSoundBank（Joseph Sardin，sound 1516，CC0）
  file: sfx/footsteps-wood.mp3
  source_url: https://bigsoundbank.com/UPLOAD/mp3/1516.mp3
  attribution: ""
  notes: 走廊逼近/侦探戏；节奏与画面脚步节拍对齐（1 步/秒基准，随情绪变速）

- id: `sfx/footsteps-run-concrete`
  category: sfx
  title: 奔跑脚步（水泥地）
  tags: [脚步, 奔跑, 追逐, footsteps, run, 追逐]
  emotion: 急促 / 危险
  mood: 追逐
  bpm: 0
  duration_seconds: 10
  license: CC0
  source: Sonniss GDC（Footsteps 类）
  file: sfx/footsteps-run-concrete.wav
  source_url: ""
  attribution: ""
  notes: 追逐段落主节奏源；速度匹配 `action_sequence` 的奔跑节拍，BPM≈140-160

- id: `sfx/door-creak`
  category: sfx
  title: 老旧门轴吱呀
  tags: [门, 吱呀, 恐怖, door, creak]
  emotion: 不安 / 悬念
  mood: 惊悚
  bpm: 0
  duration_seconds: 2
  license: CC0
  source: BigSoundBank（Creaking Door #8，sound 3211，CC0）
  file: sfx/door-creak.mp3
  source_url: https://bigsoundbank.com/UPLOAD/mp3/3211.mp3
  attribution: ""
  notes: 恐怖/悬疑开门镜头；放慢 20% + 加混响可增强诡异感

- id: `sfx/door-slam`
  category: sfx
  title: 摔门声
  tags: [门, 摔门, 冲突, door, slam]
  emotion: 愤怒 / 决裂
  mood: 冲突
  bpm: 0
  duration_seconds: 4
  license: CC0
  source: BigSoundBank（Door Slamming #1，sound 103，CC0）
  file: sfx/door-slam.mp3
  source_url: https://bigsoundbank.com/UPLOAD/mp3/0103.mp3
  attribution: ""
  notes: 争吵决裂镜头；与画面关门帧对齐，可叠加低频 thump

- id: `sfx/glass-break`
  category: sfx
  title: 玻璃破碎
  tags: [玻璃, 破碎, 爆炸, glass, break]
  emotion: 冲击 / 破碎
  mood: 破坏
  bpm: 0
  duration_seconds: 3
  license: CC0
  source: Sonniss GDC（Glass 类）
  file: sfx/glass-break.wav
  source_url: ""
  attribution: ""
  notes: 破坏镜头/动作戏；与 `effects/destroy-glass` 词条的画面提示词配套

- id: `sfx/explosion-distant`
  category: sfx
  title: 远处爆炸
  tags: [爆炸, 远处, 战争, explosion, 灾难]
  emotion: 震撼 / 灾难
  mood: 战场
  bpm: 0
  duration_seconds: 8
  license: CC0
  source: Sonniss GDC（Explosions 类）
  file: sfx/explosion-distant.wav
  source_url: ""
  attribution: ""
  notes: 战争/末日段落；低通滤波 + 延迟制造距离感，爆炸后留 1-2s 混响尾

- id: `sfx/whoosh-transition`
  category: sfx
  title: 转场风声（whoosh）
  tags: [风声, 转场, 音效, whoosh, transition]
  emotion: 利落 / 推进
  mood: 转场
  bpm: 0
  duration_seconds: 1
  license: CC0
  source: Sonniss GDC（Whoosh 类）
  file: sfx/whoosh-transition.wav
  source_url: ""
  attribution: ""
  notes: 快速转场/场景切换的声桥；与 wipe/push 转场、whip_pan 运镜配对，长 0.5-1.2s

- id: `sfx/heartbeat-tense`
  category: sfx
  title: 心跳声（紧张）
  tags: [心跳, 紧张, 惊悚, heartbeat, 悬疑]
  emotion: 惊惧 / 压迫
  mood: 紧张
  bpm: 0
  duration_seconds: 21
  license: CC-BY
  source: Wikimedia Commons（File:Heartbeat.ogg，HerbertBoland，CC BY 3.0）
  file: sfx/heartbeat-tense.ogg
  source_url: https://commons.wikimedia.org/wiki/Special:FilePath/Heartbeat.ogg
  attribution: "Heartbeat by HerbertBoland — CC BY 3.0"
  notes: 惊悚揭示前/危险逼近段落；与特写同步。CC-BY 必须署名
