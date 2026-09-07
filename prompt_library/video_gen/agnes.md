# Agnes 2.5 Flash（仓库现行默认，中国站）

API 面：`agnes_v25`。模型 `agnes-video-2.5-flash`。默认 Base `https://api.agnes-ai.cn/v1`。

密钥：`AGNES_API_KEY` 或 `AGNES_CN_API_KEY`。Base 覆盖：`AGNES_API_BASE_URL` 或 `AGNES_BASE_URL`。只有把 Base 显式设成 `apihub.agnes-ai.com` 才走国际站。

回滚 2.0：`AGNES_VIDEO_MODEL=agnes-video-v2.0`（`agnes_v20`，`passthrough=true`，adapter 不改写 2.0 分节）。`agnes-video-2.5` 视为 Flash 别名，不再发非 Flash 2.5。

## 接口合同

- 创建：`POST {base}/videos`
- 查询：`GET {origin}/agnesapi?video_id=<id>&model_name=agnes-video-2.5-flash`（origin 去掉 `/v1`；keyframe/reference 必须带 model_name）
- `seconds` 为 JSON **字符串** `"4"`–`"12"`；`size` 仅 `"720P"`
- mode 互斥：`text` / `keyframe` / `reference`。剧情主路径用 `reference`（`images[]` 最多 5 张 https + `<Picture N>`）
- Flash **禁止** `videos[]`（HTTP 400）。超 12s 切段后 ffmpeg 拼接；同场衔接靠同一套定妆图，不要尾帧静图混进 reference
- 禁止：`negative_prompt`、宽高帧率、`first_frame` 与 `images` 同时出现
- 占位符从 1 编号。官方英文六段式只作对照，**不是**本仓库必填作文模板

2.0 已退役：`_payload_v20` 仅环境变量回滚。不要用 2.0 的【台词】【音频锁定】去「优化」2.5 Flash。
