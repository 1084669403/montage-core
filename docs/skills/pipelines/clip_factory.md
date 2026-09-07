# clip_factory（相对 cinematic 的差异）

同 7 阶段。默认竖屏 `douyin_vertical`、`cut_only`。这两项不变。

**script 阶段额外产物 `clip_plan`**（必填 `source_path` + `cuts[]`）。由导演根据选题写下切点，不是 `scene_detect` 自动生成剧本。`scene_detect` 只是辅助看切点，结果要人工改写进 `clip_plan`。

**已有切片 clip 时**：`python -m montage produce <项目目录>` 走与 W0 相同的配乐链（`soundtrack_planner` → `place_audio` → `assemble` → finish/release → `export_bundle`）。缺 clip **失败**，不跑 `shot_runner`，不能 `--retry` 生成。

compose 硬切拼接短切片，不要把长片整段当 cinematic 转场片。`voice_director` 不进本管线。publish.tools 只有 `export_bundle`（不含 `subtitle_builder`）。
