# Seedance / 即梦方舟

API 面：`seedance_25`、`seedance_20_pro`。`video_loop=ark`（或 `seedance`）才走方舟；`volcengine` 仍是 v30 HMAC。

## 方言（adapter 外包，不改 builder 正文）

- 语言：中文叙事。长镜头用时间戳分段（例：`0-8s …；8-20s …`），一场多镜靠**一段时间**，不要把不同 `location_id` 或 `cut=hard` 拼进一次调用。
- 引用：`@图片1用于角色外貌，不采用背景`。职责写在引用句，不要让模型吃参考图的背景。
- 对白：`{台词原文}`。默认**不要**写 `【字幕】`（烧字幕走 `subtitle_builder`）。
- 音效/音乐符号：`(音乐)` `<音效>` 仅当分镜明确要求；adapter 不自动发明。
- 负向：有 `negative_prompt` 通道。
- 字数：2.5 约 4000；2.0 Pro 约 2500。**禁止**套 v30 的 800 硬截断。
- 时长：2.5 为 4–30s（range）；2.0 Pro 为 4–15s。首尾帧锁定时请求 `ratio=adaptive`，成片后再 `apply_profile`。
- 水印：生产默认关。

## 返工（P5）

成片仍有公网 URL 时，`--retry` 走 **edit**（`duration=-1`，`ratio=adaptive`，`content[]` 里 `@视频1` / `role=reference_video`），不要再填首尾帧（generate 注入器会丢掉成片）。时长要比成片长时走 **extend**。URL 过期或只有本地 mp4 → 整镜重抽。提示词用「修改@视频1……其余保持不变」，不要把整段 generate 词当编辑指令。

## 与 v30 的边界

`jimeng_v30` 继续中文结构 +「无字幕无文字无水印」+ 800 字。recamera 只属于 v30。方舟未开通或 2.5 失败时降级 v30，不要把 2.5 网格写进 `policy_for_loop("jimeng")`。
