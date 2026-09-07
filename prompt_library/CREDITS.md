# CREDITS — 词条来源与授权

本词库内容为**人工精选、改写、结构化**而成，原始素材来自以下开源项目（均为 MIT License）。

## 来源仓库

| 仓库 | 作者 | 许可证 | 使用内容 |
|------|------|--------|----------|
| `Wayhhow/ai-video-shot-prompt-skill` | Wayhhow | MIT | 风格核心关键词、去 AI 味词、限制词、光线描述、色彩影调、动作场景模板 |
| `jijiutong/ai-visual-director` | jijiutong / AI Visual Director Contributors | MIT | 镜头语言（景别/运镜/角度/焦段）、身体语言与姿态、环境与世界观、特效设计数据 |
| `MapleShaw/seedance2.0-prompt-skill` | MapleShaw / Seedance Prompt Skill Contributors | MIT | 提示词结构、镜头-场景映射思路、风格化参考 |
| `Shanyin-ai/shanyin-director-master` | Shanyin-ai | MIT | directors 库：类型风格模板 + 交叉组合的方法论（人工结构化改写为类型化范式） |
| `wuwangzhang1216/DirectorSKILL` | wuwangzhang1216 | MIT | directors 库：「风格参数 + 反例机制」的方法论（仅借鉴机制，不照抄其导演风格描述，遵守禁写导演名约束） |
| `0xsline/short-drama` | 0xsline | MIT | scripts 库：节奏曲线（起-升-暴-决）、钩子系统、转折设计方法论（剔除付费卡点/爽感矩阵等短剧特供项） |
| `Vi7QY/screenwriter-skill` | Vi7QY / 贞智影 | MIT | scripts 库：对白潜台词/推动行动、情绪兑现等通用编剧铁律（筛选后仅采纳正确且通用的部分） |
| `FableCut/FableCut-Transitions` | FableCut | MIT | edits 库：17 种转场命名与方向（dissolve/wipe/push/zoom 等）的通用剪辑方法论（人工结构化改写为范式） |
| `digitallyinduced/beat-synced-edit` | digitallyinduced | MIT | edits 库：节拍同步剪辑 / 音乐能量-切点映射的概念（节奏曲线、切点放置） |

## stars 分级筛选说明（2026-08-15）

- **直接借鉴（高 stars ≥500）**：`0xsline/short-drama`（684）——剧本节奏/钩子方法论直接采纳。
- **逐条核对（中 stars 100-499）**：`Shanyin-ai/shanyin-director-master`（325）——类型模板方法论
  采纳；`worldwonderer/drama-skills`（377）——连续性契约概念纳入（详见 scripts 库）。
- **先筛选验证（低 stars <100）**：`wuwangzhang1216/DirectorSKILL`（44）——仅借鉴「风格参数 +
  反例」的机制（通用方法论，正确），**不照抄其 20 位导演的具体风格描述**（避免事实错漏 + 遵守禁写
  导演名）；`Vi7QY/screenwriter-skill`（36）——其「对白精炼/潜台词/情绪兑现」是通用编剧常识
  （正确），纳入；**剔除**其针对特定平台红果 Top100 的审稿流程细节（与 OpenMontage 通用剧本需求
  不符）；`digitallyinduced/beat-synced-edit`（低）——其「音乐能量-切点映射」是通用剪辑常识，
  仅采纳节拍同步/切点放置的方法概念（正确），**剔除**其具体 After Effects/剪辑软件脚本实现细节
  （与 OpenMontage 的 FFmpeg 落地不符）。
- **edits 库转场来源（直接借鉴，FableCut 转场集广泛认可）**：`FableCut/FableCut-Transitions`——
  仅采纳 17 种转场的**命名/方向/适用语义**作为剪辑决策范式（通用方法论），**不照抄**其任何
  成品转场包/素材/数值；与 `video_stitch` 现有 cut/crossfade/fade 原语对齐，avoid 引入
  未实现的转场名。
- **剔除项**：`0xsline/short-drama` 的付费卡点/爽感矩阵/合规审查（国内短剧特供，与通用剧本需求不符）；
  纯海外分发流程；任何可照抄的成品台词（违反 verbatim 约束）。

## 公有领域剧本库（screenplays 分类，2026 新增）

`screenplays/` 的 12 条场景范式分析基于以下**公有领域（Public Domain）**剧作：
仅引用极短原文片段做语感参考，范式分析文本为原创；成片剧本须原创，禁止照抄。

| 出处 | 作者 | 公有领域依据 |
|------|------|--------------|
| 《茶馆》 | 老舍（1966 年逝世） | 中国大陆保护期作者死后 50 年，2017-01-01 起公有 |
| 《窦娥冤》《西厢记》 | 关汉卿、王实甫（元代） | 13 世纪作品，公有 |
| 《牡丹亭》 | 汤显祖（明代） | 1598 年成书，公有 |
| 《哈姆雷特》 | 莎士比亚 | 约 1601 年，公有；中文引文参考朱生豪译本（1995 年起公有） |
| 《人民公敌》 | 易卜生（1906 年逝世） | 公有；引文为意译，不采用现代译本 |
| 《樱桃园》 | 契诃夫（1904 年逝世） | 公有；引文为意译 |
| 《过客》 | 鲁迅（1936 年逝世） | 1987-01-01 起公有 |
| 《关汉卿》 | 田汉（1968 年逝世） | 2019-01-01 起公有 |

**约束**：外国剧作的中文引文仅使用已公有译本或自译；仍在版权期的现代译本一律不引用。

## 使用原则

1. 仅抽取词条内容并**结构化改写**，未整仓复制仓库文件。
2. 词条仅作"参考上下文"，供 LLM 改编，不直接作为最终 prompt 输出。
3. 转载/再分发本词库时，须保留上述 MIT 版权声明与本 CREDITS 文件。

## MIT 版权声明

各来源项目保留其各自的 MIT License 版权声明，详细文本见各仓库 LICENSE 文件。

## 综合来源

部分词条综合自 OpenMontage 自有内容：
- `OpenMontage/skills/creative/video-gen-prompting.md`
- `OpenMontage/skills/pipelines/chinese/cinematic-language.md`
- `OpenMontage/skills/creative/provider-prompt-rules.md`
- `OpenMontage/styles/chinese-elegance.yaml`（风格 playbook 锚点）

## 语言与英文层来源（A/B 验证后）

- 词条全部为**中文**（含英文 `tags` 作为检索关键词），改编后产出中文 `visual_details` 字段。
- 英文画质词（`ultra-sharp / high detail / photorealistic texture` 等）与英文
  `negative_prompt` 由 `OpenMontage/lib/shot_prompt_builder.py` 的
  `_ENGLISH_VISUAL_BASELINE` / `_FRAME_CONSISTENCY_EN` / `_ENGLISH_NEGATIVE_PROMPT`
  **builder 常量**统一提供（非词库内容），是英文质量/语音守卫的单一事实源，
  供 `default_english_negative_prompt()` 引用。词库不承担英文画质词条。

## W1 分库（原创改写，2026-08）

`characters/`（8）与 `dialogue/`（6）为 montage-core 原创外貌/口吻锚点，**不是成品台词**。
`actions/beat-grab-collar` 等接触动作从既有 MIT 动作范式拆细，禁止空词「打架」。
赛璐璐技法沿用已有 `styles/anime-cel`，不新增在世作者具名 playbook。

## W2 风格族库（树形五大族，2026-09）

`styles/` 追加 12 条新词条 + 4 条既有条目补 `genre` 族字段，构成树形五大族 16 叶：
`chinese-traditional`（4）/ `animation-film`（4，含既有 anime-cel）/ `comics-illustration`（3）/
`photoreal`（2 基础=既有 cinematic-film/noir，另加 2 条 cinematography 增强锚点）/
`techno-punk`（3，含既有 cinematic-cyberpunk）。防污染三原则：叶子自包含整条返回、
单项目单叶子（写 `bible.style_lock`）、风格头前置负向词收尾；检索按 `genre` 过滤防跨族混搭。

| 来源仓库/页面 | 许可证 | 使用内容 |
|------|--------|----------|
| `williamjxj/LianHuanAI` | MIT | 21 位画师风格描述（刘继卣/胡若佛/凌涛等，结构化改写为画师锚点）、白描 STYLE_ANCHOR 英文锚点、中英双语负向词、风格头前置实测结论 |
| `vaulthunt3r/ComfyUI-Style-Prompts-Collection` | MIT | Cartoon/Low Poly/Mech/Neon Line Art 等族的正负向 prompt 结构（人工改写，未照抄整段） |
| `threerocks/hand-drawn-styles` | MIT | 吉卜力反堆细节实测增强、xkcd 极简线条讲解配方（负向约束前置的踩坑经验） |
| `krusemediallc/arcads-claude-code` | MIT | Pixar 式 3D 动画光效/材质/景深公式与负面词表（改写，未照抄品牌文案） |
| `ZaynJarvis/aesthetics` | MIT | 337 风格统一模板「Create {{SUBJECT}} in X style / Avoid」的结构思路；赛博朋克场景氛围词改写 |
| `NickPittas/DirectorsConsole` | MIT | 真人电影实机/镜头/胶片/光效预设的方法论（仅借鉴概念，实拍配置词人工重写） |
| `madebysaira/cinematic-ai-prompts` | MIT | Kling/Veo/Runway/Seedance 逐模型调优思路与负向词（方法论参考） |
| promptsref.com sref 拆解页 | 仅方法论参考 | 朱砂木刻（红单色线描/雕版断线/纸纹）与赛博朋克漫画的风格拆解思路，词条文本为原创改写 |

**约束**：画师锚点仅用已故连环画画师的技法描述（刘继卣 1990 卒、胡若佛 1980 卒、凌涛 2001
卒）；在世作者/品牌名只入 tags 检索词不入 prompt 正文（Pixar→"Pixar-inspired"、"新海诚式"→
"日式唯美光效"）；LzyPrompt 布光四段式为通用摄影常识直接采纳不计入来源表。

