# BGM — 背景音乐索引（FreePD CC0 / Kevin MacLeod CC-BY）

> 来源：① [FreePD](https://freepd.cn/)（CC0 公有领域，无需署名）；②
> [incompetech.com](https://incompetech.com/)（Kevin MacLeod，**CC BY 3.0**，必须署名）。
> 本文件是**情绪-节奏档案**：每条描述成片某段落需要的音乐（情绪/速度/乐器）。
> `file:` 是落盘目标路径（相对 `assets/`，如 `bgm/dark-drone.mp3`）；文件不在则
> `available=false`。`source_url` 只钉已 HEAD 验证的直链；`asset_retriever operation=resolve`
> 下载到该路径，**不改本文件**。无直链时 `python assets/scripts/fetch_assets.py --bgm`。
> 钉选曲可能是同等许可的替代（例如 MacLeod 代替 FreePD 空槽），以 `license` / `attribution` 为准。
>
> 使用：`asset_retriever` 按情绪/BPM 检索；`bpm` 仅供卡点剪辑参考，不参与选曲；
> `mix_audio` 混音时音乐电平低于旁白（默认 0.25）。

## 情绪氛围 (Mood & Ambience)

- id: `bgm/dark-drone`
  category: bgm
  title: 暗黑氛围 drone（悬疑/压迫）
  tags: [氛围, 暗黑, 悬疑, 压迫, drone, 惊悚]
  emotion: 压抑 / 不安
  mood: 悬疑
  bpm: 60
  duration_seconds: 120
  license: CC-BY
  source: incompetech（Kevin MacLeod，Darkest Child；配方槽暗黑 drone 的替代曲）
  file: bgm/dark-drone.mp3
  source_url: https://incompetech.com/music/royalty-free/mp3-royaltyfree/Darkest%20Child.mp3
  attribution: "Music: Darkest Child by Kevin MacLeod (incompetech.com) — CC BY 3.0"
  notes: 悬疑段落铺底；低音压迫感，与 low-key 光、深阴影镜头同构。CC-BY 必须署名

- id: `bgm/horror-ambient`
  category: bgm
  title: 恐怖氛围（惊悚）
  tags: [恐怖, 惊悚, 氛围, horror, 诡异]
  emotion: 惊惧 / 诡异
  mood: 恐怖
  bpm: 50
  duration_seconds: 120
  license: CC0
  source: FreePD（Horror 分类）
  file: bgm/horror-ambient.mp3
  source_url: ""
  attribution: ""
  notes: 惊悚揭示前段落；配合心跳音效（sfx/heartbeat-tense）叠层使用，音量渐入

- id: `bgm/calm-piano`
  category: bgm
  title: 平静钢琴（治愈/回忆）
  tags: [钢琴, 平静, 治愈, 回忆, piano, 抒情]
  emotion: 温柔 / 怀念
  mood: 抒情
  bpm: 70
  duration_seconds: 120
  license: CC-BY
  source: incompetech（Kevin MacLeod，Gymnopedie No 1；配方槽平静钢琴的替代曲）
  file: bgm/calm-piano.mp3
  source_url: https://incompetech.com/music/royalty-free/mp3-royaltyfree/Gymnopedie%20No%201.mp3
  attribution: "Music: Gymnopedie No 1 by Kevin MacLeod (incompetech.com) — CC BY 3.0"
  notes: 回忆/温情段落；钢琴留白多，适合对白下轻声铺底。CC-BY 必须署名

- id: `bgm/emotional-strings`
  category: bgm
  title: 弦乐抒情（离别/牺牲）
  tags: [弦乐, 抒情, 离别, 牺牲, strings, 悲情]
  emotion: 哀伤 / 崇高
  mood: 悲情
  bpm: 65
  duration_seconds: 120
  license: CC0
  source: FreePD（Strings / Emotional 分类）
  file: bgm/emotional-strings.mp3
  source_url: ""
  attribution: ""
  notes: 离别/牺牲/情绪兑现段落；情绪峰值处可 +3dB 推起再渐落

- id: `bgm/warm-acoustic`
  category: bgm
  title: 温暖原声（日常/治愈）
  tags: [原声吉他, 温暖, 日常, 治愈, acoustic, 温馨]
  emotion: 温暖 / 松弛
  mood: 日常
  bpm: 90
  duration_seconds: 120
  license: CC0
  source: FreePD（Acoustic / Folk 分类）
  file: bgm/warm-acoustic.mp3
  source_url: ""
  attribution: ""
  notes: 日常段落/开场建世；轻快不抢戏，适合台词下的背景

## 节奏与能量 (Pacing & Energy)

- id: `bgm/tense-pulse`
  category: bgm
  title: 紧张脉冲（追逃/逼近）
  tags: [紧张, 脉冲, 追逐, 逼近, pulse, 动作]
  emotion: 急促 / 危险
  mood: 追逐
  bpm: 120
  duration_seconds: 90
  license: CC0
  source: FreePD（Action / Chase 分类）
  file: bgm/tense-pulse.mp3
  source_url: ""
  attribution: ""
  notes: 追逐/对峙段落；BPM 与奔跑节拍（sfx/footsteps-run-concrete）对齐，切点落重拍

- id: `bgm/epic-orchestral-rise`
  category: bgm
  title: 史诗管弦推进（高潮）
  tags: [管弦, 史诗, 高潮, 推进, orchestral, epic]
  emotion: 壮阔 / 激昂
  mood: 高潮
  bpm: 110
  duration_seconds: 120
  license: CC-BY
  source: incompetech（Kevin MacLeod，Heroic Age；配方槽史诗管弦的替代曲）
  file: bgm/epic-orchestral-rise.mp3
  source_url: https://incompetech.com/music/royalty-free/mp3-royaltyfree/Heroic%20Age.mp3
  attribution: "Music: Heroic Age by Kevin MacLeod (incompetech.com) — CC BY 3.0"
  notes: 决战/hero_moment 段落；管弦渐强对齐情绪顶点。CC-BY 必须署名

- id: `bgm/techno-beat`
  category: bgm
  title: 电子节拍（卡点/城市）
  tags: [电子, 节拍, 卡点, 城市, techno, beat]
  emotion: 明快 / 现代
  mood: 城市
  bpm: 128
  duration_seconds: 120
  license: CC0
  source: FreePD（Electronic / Dance 分类）
  file: bgm/techno-beat.mp3
  source_url: ""
  attribution: ""
  notes: 城市蒙太奇/卡点剪辑段落；重拍与切点严格对齐，适合 fast-cut 蒙太奇

- id: `bgm/victory-heroic`
  category: bgm
  title: 胜利号角（结局/凯旋）
  tags: [号角, 胜利, 结局, 凯旋, heroic, 收尾]
  emotion: 昂扬 / 圆满
  mood: 结局
  bpm: 100
  duration_seconds: 90
  license: CC0
  source: FreePD（Heroic / Fanfare 分类）
  file: bgm/victory-heroic.mp3
  source_url: ""
  attribution: ""
  notes: 结局/凯旋段落；可配合 fade_black 收尾，最后一个音符落到黑场

## 国风与特色 (Chinese & Character)

- id: `bgm/chinese-traditional`
  category: bgm
  title: 传统国风（笛箫/古筝）
  tags: [国风, 古筝, 笛, 传统, chinese, 古风]
  emotion: 典雅 / 悠远
  mood: 古风
  bpm: 75
  duration_seconds: 120
  license: CC-BY
  source: incompetech（Kevin MacLeod，Eastern Thought）
  file: bgm/chinese-traditional.mp3
  source_url: https://incompetech.com/music/royalty-free/mp3-royaltyfree/Eastern%20Thought.mp3
  attribution: "Music: Eastern Thought by Kevin MacLeod (incompetech.com) — CC BY 3.0"
  notes: 古装/仙侠段落；笛声悠远适合远景空镜。CC-BY 必须署名

- id: `bgm/gentle-ukulele`
  category: bgm
  title: 轻快尤克里里（温馨/短片）
  tags: [尤克里里, 轻快, 温馨, 短片, ukulele, 治愈]
  emotion: 轻松 / 治愈
  mood: 轻快
  bpm: 100
  duration_seconds: 90
  license: CC-BY
  source: incompetech（Kevin MacLeod，Carefree；配方槽轻快尤克里里的替代曲）
  file: bgm/gentle-ukulele.mp3
  source_url: https://incompetech.com/music/royalty-free/mp3-royaltyfree/Carefree.mp3
  attribution: "Music: Carefree by Kevin MacLeod (incompetech.com) — CC BY 3.0"
  notes: 治愈短片/片头段落；CC-BY 必须署名（成片 credits 用 attribution 字段）
