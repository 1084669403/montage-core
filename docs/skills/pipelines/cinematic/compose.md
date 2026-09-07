# cinematic / compose

产出：`edit_decisions` + `render_report`（**不要**把 `compose_plan` 当 completed 必填产物）。

| 步骤 | 工具 | 要点 |
|------|------|------|
| 1 配乐 | `soundtrack_planner`（建议 `resolve=true`） | 写 `artifacts/soundtrack.json`；整片一条 BGM；纪录片 `skip_bgm`；钉选曲按 INDEX `source_url` 下载。换货 `asset_retriever remote=true` 进项目 assets |
| 2 编译 | `compose_planner` | 读分镜+manifest+soundtrack，调 `edit_advisor`，写出 `edit_decisions` |
| 3 静图 | `compose_planner realize=true` | 仅 image clip 跑 ken_burns，**原地改** `cuts[].clip_path`；不要为改路径走 overwrite 重编 |
| 4 音效 | `place_audio` | 默认读 soundtrack 产物；SFX 叠到 clip；assemble 跟 `mix_source_audio` |
| 5 成片 | `ffmpeg_compose assemble` | **只读** `edit_decisions`；单条 BGM 裁到片长并淡入淡出 |
| 6–7 收尾 | `produce` 的 **finish** | LUT / 显式 profile / 2s 片头 / lower-third / SRT 旁路。**不要**手搓 `apply_lut`、`apply_profile`、`burn_subtitles` |

`ffmpeg_compose assemble` 默认打开 `ducking` / `loudnorm`（无旁白时 loudnorm 可能不跑，属现行为）。
**不要**让 assemble 自动 `apply_profile`。平台档案只认 `proposal_packet.output_profile` 或 `produce --profile`；管线 `default_profile` **不是**触发源。
`render_runtime` 若不是 `ffmpeg` 必须失败。
已有精修的 edit_decisions 默认不覆盖（`overwrite=true` 才重编骨架）。
`title_card` 等图形 `render_kind` 本轮降级 `ai_clip`（2s 片头是 finish 的 FFmpeg 操作，不是这个 render_kind）。
默认只写 `renders/final.srt`；`--burn-subs` 才烧进像素。缺字体烧录失败不 fail。
**成片入口** `python -m montage produce <项目目录>`（前提：磁盘上已有 clip）。单步调试仍可用 `python -m montage run`（直接 import 不读 `.env`）。
