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

## Flash 与 2.0 差异速查

| 维度 | 2.5 Flash（现行） | 2.0（仅回滚） |
| --- | --- | --- |
| 模型 | `agnes-video-2.5-flash` | `agnes-video-v2.0` |
| 时长 | `seconds` 字符串 `"4"`–`"12"` | `num_frames` + `frame_rate` |
| 参考图 | `images[]` ≤ 5 | `extra_body.image` + `mode=keyframes` |
| 参考音频 | `audios[]` ≤ 3 | 无 |
| 参考视频 | 不支持（`videos[]` 400） | 无 |
| 负向提示 | 不支持 | `negative_prompt` |
| `aspect_ratio` | 六值公共参数 | 用 `width`/`height` |

## aspect_ratio（公共参数）

文档六值，全模式适用：`21:9` / `16:9` / `4:3` / `1:1` / `3:4` / `9:16`（缺省 `16:9`）。
仓库按 `proposal_packet.output_profile` 推导（`*_vertical` → `9:16`、`cinematic_21_9` → `21:9`，否则 `16:9`），
也可用 `AGNES_RATIO` 覆写；覆写值须落在上述六值（非法值自动落回档案推导）。
官方注：16:9 / 720P 实测输出约 `1280x704`，需要精确画布由成片 finish 缩放补边。

## 轮询

- 间隔 1–2s（仓库固定 1.5s）；带 `model_name` 查询。
- 429 时限流退避（指数，上限 30s）；失败态立即停，不空转。

## 限流与配额（Token Plan FAQ）

访问档位由 `AGNES_ACCESS_TYPE` 决定（唯一来源）：`default` / `enterprise` / `tokenplan`。
**不设即按免费 / 默认档**，代码不会从密钥或额度推断 Token Plan。同类型的多个密钥共享同一限制池、不叠加。

| 档位 | 视频实际 RPM | 图片实际 RPM（1K/2K/3K/4K） | 每日订阅配额 |
| --- | ---: | --- | --- |
| `default` | 1 | 20 / 10 / 1 / 1 | 无 |
| `enterprise` | 2 | 40 / 20 / 1 / 1 | 无 |
| `tokenplan` | 5 | 100 / 80 / 1 / 1 | 图片 4000 张/天；视频 500 秒/天 |

- RPM 控制发车节奏（pacing），订阅配额是每日总量；两者**同时生效**。
- 配额计数存用户级 `~/.montage/agnes_usage.json`（`MONTAGE_AGNES_USAGE_PATH` 覆写），按档位分桶、跨天归零。
- 仓库**只统计 + 告警，不阻断出片**；接近上限时 produce dry_run 给出排片提示（`field=agnes_quota`）。
- `.env` **不覆盖已存在的 shell 变量**：切档前先 unset 同名变量，否则写 `.env` 不生效。
