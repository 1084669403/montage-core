# cinematic / publish

产出：`artifacts/publish_log.json`（必填 `status`：draft/exported/published）。

已有 clip 时用 `python -m montage produce <项目目录>` 会在拼片后跑 **release**（封面抽帧 + 三平台简介模板 + `publish_log=exported`）再 `film_health`（整片 ffprobe）再 `export_bundle`（zip 进项目 `exports/`，含 `CREDITS.txt`、封面 jpg、字幕 srt）。`film_health` critical 会挡住 zip；`--skip-export` 仍写出健康报告但不挡。也可单独 `run export_bundle` / `run release_pack` / `run film_health`。

`status=exported` 表示打好包；**打包不等于 `published`**。已是 `published` 时 produce 不得降级。简介是模板截断，不是 LLM。门禁阶段需审批。
`subtitle_builder` **不**进 publish.tools；字幕在 finish 写旁路。
