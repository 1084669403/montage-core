# LUTs — 原创调色表（.cube，随仓库分发）

> 由 `scripts/make_luts.py` 程序化生成的**原创调色表**（MIT，零版权风险），
> 供 ffmpeg `lut3d` 滤镜使用（见 `montage/compose/ffmpeg_engine.py` 的 `apply_lut`）。
> 文件本体在 `assets/luts/*.cube`，**已随仓库分发**（无需下载）。
>
> 使用：`asset_retriever` 按情绪/风格检索；`apply_lut` 在整片装配后统一调色，
> 实现跨镜头色调一致（"电影感"的关键）。可再叠加轻微 vignette/裁切。

- id: `luts/teal-orange`
  category: luts
  title: 青橙对比（电影感标配）
  tags: [调色, 青橙, 电影感, teal, orange, 对比]
  emotion: 冷峻 / 大片
  mood: 电影
  bpm: 0
  duration_seconds: 0
  license: MIT
  source: montage-core 原创（scripts/make_luts.py）
  file: luts/teal-orange.cube
  attribution: ""
  notes: 暗部偏青、高光偏橙；动作/悬疑/商业片通用，是"电影感"首选

- id: `luts/dark-moody`
  category: luts
  title: 暗黑电影感（悬疑/压迫）
  tags: [调色, 暗黑, 悬疑, 压迫, dark, moody]
  emotion: 压抑 / 紧张
  mood: 悬疑
  bpm: 0
  duration_seconds: 0
  license: MIT
  source: montage-core 原创（scripts/make_luts.py）
  file: luts/dark-moody.cube
  attribution: ""
  notes: 压暗 + 降饱和 + 深阴影；与 low-key 光、黑色电影（noir）风格搭配

- id: `luts/warm-film`
  category: luts
  title: 胶片暖调（复古/怀旧）
  tags: [调色, 胶片, 暖调, 复古, 怀旧, film, vintage]
  emotion: 温暖 / 怀旧
  mood: 复古
  bpm: 0
  duration_seconds: 0
  license: MIT
  source: montage-core 原创（scripts/make_luts.py）
  file: luts/warm-film.cube
  attribution: ""
  notes: 高光暖黄 + fade blacks + 轻微褪色；年代剧/回忆段落/爱情片

- id: `luts/cool-clean`
  category: luts
  title: 日系清冷（治愈/清新）
  tags: [调色, 日系, 清冷, 治愈, 清新, cool, clean]
  emotion: 清爽 / 宁静
  mood: 治愈
  bpm: 0
  duration_seconds: 0
  license: MIT
  source: montage-core 原创（scripts/make_luts.py）
  file: luts/cool-clean.cube
  attribution: ""
  notes: 高明度 + 低对比 + 冷调；治愈/日常/青春片

- id: `luts/bw-high-contrast`
  category: luts
  title: 黑白高反差（黑白片）
  tags: [调色, 黑白, 高反差, 单色, bw, monochrome]
  emotion: 冷峻 / 纪实
  mood: 黑白
  bpm: 0
  duration_seconds: 0
  license: MIT
  source: montage-core 原创（scripts/make_luts.py）
  file: luts/bw-high-contrast.cube
  attribution: ""
  notes: 去色 + S 曲线强化黑白两端；黑白片/回忆闪回/文艺段落

- id: `luts/muted-documentary`
  category: luts
  title: 纪实低饱和（纪录片）
  tags: [调色, 纪实, 低饱和, 纪录片, muted, documentary]
  emotion: 真实 / 克制
  mood: 纪实
  bpm: 0
  duration_seconds: 0
  license: MIT
  source: montage-core 原创（scripts/make_luts.py）
  file: luts/muted-documentary.cube
  attribution: ""
  notes: 中性色温 + 降饱和 + 保细节；纪录片/访谈/写实段落
