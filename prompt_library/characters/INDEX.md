# Characters — 外貌锚点

检索用途：写 `series_bible.characters[].appearance` 时参考。一条一个 contrast_group，
**同一集不要复用同组**。只给可见锚点（发/疤/眼镜/配饰），不是完整人物卡，禁止成品脸谱照抄。

- id: `characters/scar-glasses`
  category: characters
  title: 左眉疤+铁框眼镜
  tags: [人物, 外貌, 疤, 眼镜, 冷硬]
  contrast_group: scar-glasses
  medium: film
  genre: thriller
  emotion: 克制 / 警惕
  action_density: medium
  shot_kind: first_frame
  prompt: "黑发齐耳，左眉一道旧疤，铁框眼镜，下颌线硬"
  notes: 冷硬主角/侦探；不要写成「东亚青年男性」空话

- id: `characters/silver-earrings`
  category: characters
  title: 银白短发+三耳环
  tags: [人物, 外貌, 银发, 耳环, 张扬]
  contrast_group: silver-earrings
  medium: film,anime
  genre: action
  emotion: 张扬 / 不驯
  action_density: high
  shot_kind: first_frame
  prompt: "银白短发，右耳三枚耳环，锁骨一小片纹身"
  notes: 与 scar-glasses 同场时靠发色和耳饰区分

- id: `characters/dimple-round`
  category: characters
  title: 圆脸浅涡
  tags: [人物, 外貌, 酒窝, 圆脸, 亲和]
  contrast_group: dimple-round
  medium: film,spoken
  emotion: 亲和 / 松弛
  action_density: low
  shot_kind: first_frame
  prompt: "圆脸，右颊浅酒窝，齐刘海，耳侧一颗小痣"
  notes: 讲解出镜或配角；避免和 long-hair-mole 同集

- id: `characters/buzz-burn`
  category: characters
  title: 寸头+颈侧烫伤
  tags: [人物, 外貌, 寸头, 烫伤, 退伍]
  contrast_group: buzz-burn
  medium: film
  genre: action
  emotion: 压抑 / 强硬
  action_density: medium
  shot_kind: first_frame
  prompt: "寸头，颈侧不规则烫伤疤，鼻梁略歪"
  notes: 退伍/打手；疤在颈侧不要写成脸部大面积烧伤

- id: `characters/long-mole`
  category: characters
  title: 长发+唇下痣
  tags: [人物, 外貌, 长发, 痣, 文静]
  contrast_group: long-mole
  medium: film,anime
  emotion: 克制 / 观察
  action_density: low
  shot_kind: first_frame
  prompt: "黑长直发过肩，唇下正中一颗痣，浅色细框眼镜"
  notes: 文职/情报；与 scar-glasses 都戴眼镜时改框形或去掉一方眼镜

- id: `characters/freckle-cap`
  category: characters
  title: 雀斑+鸭舌帽
  tags: [人物, 外貌, 雀斑, 帽子, 少年]
  contrast_group: freckle-cap
  medium: film,anime
  emotion: 好奇 / 慌张
  action_density: medium
  shot_kind: first_frame
  prompt: "浅雀斑，深色鸭舌帽压眉，门牙微缝"
  notes: 少年跑腿；帽子是 silhouette 锚点

- id: `characters/gray-beard`
  category: characters
  title: 两鬓灰+短须
  tags: [人物, 外貌, 灰发, 胡须, 权威]
  contrast_group: gray-beard
  medium: film,documentary
  emotion: 沉稳 / 压迫
  action_density: low
  shot_kind: first_frame
  prompt: "两鬓灰白，修剪整齐的短须，法令纹深"
  notes: 上司/长辈；不要用「中年男性」代替

- id: `characters/pixie-hetero`
  category: characters
  title: 碎发+异色瞳
  tags: [人物, 外貌, 碎发, 异色瞳, 疏离]
  contrast_group: pixie-hetero
  medium: anime,film
  genre: sci-fi
  emotion: 疏离 / 锐利
  action_density: medium
  shot_kind: first_frame
  prompt: "碎齐耳发，左棕右灰异色瞳，锁骨细链"
  notes: 科幻/异能；异色瞳必须在定妆里写进 do_not_change
