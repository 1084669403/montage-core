# cinematic / proposal

产出：`artifacts/proposal_packet.json`（必填 `concept`）。

建议字段：

- `playbook`：可空。可用 `style_matcher`（query=主题+风格词）拿推荐后再写入。
  cinematic 默认不绑；口播用 `spoken_explain`（不要写 talking_head）。
- `output_profile`：横屏用 `youtube_landscape`；口播竖屏用 `douyin_vertical`。
- `render_runtime`：只能 `ffmpeg`。
- `video_loop`：`agnes` | `volcengine` | `dashscope` | `kling` | `ark`（`seedance` 同义）。只锁图+视频。**`ark` 视频走方舟 Seedance，定妆图仍即梦。** 配了 `ARK_API_KEY` 不会抢走 `volcengine`。
- `video_surface`：可选，覆盖本环内 API 面（如 `seedance_25` / `kling_omni_30`），必须属于当前 loop 家族。
- `allowed_providers`：与 video_loop 二选一即可。
- `budget_ceiling_usd`：有值则 `montage run` 超预算硬停。

门禁阶段：需要 `--approved`。写决策日志 `category=video_loop`。
