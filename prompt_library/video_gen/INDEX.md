# 视频生成提示词（按 API 面）

给导演 Agent 的知识页，不是词库检索条目（不计入 170 条）。生成路径只许 `python -m montage produce`。

## 五步（进入生成路径之前定死）

1. **读 caps** — `video_surface(api_id)` / 当前接线用 `video_caps(tool=…)`。看 `native_audio`、`multi_shot`、`duration_policy`、`requires_first_frame`。
2. **读 profile** — `prompt_profile(api_id)`：语言、字数、引用语法、禁忌、`passthrough`。
3. **builder** — `visual_prompt_builder` / `build_shot_prompt_pair`。Agnes 闭环 `agnes_audio=True`、`provider_max_chars=3000`。即梦 v30 才 `jimeng_prompt=True`（800 字）。**不要**用 v30 的 800 去截 Seedance 2.5。
4. **adapter** — `adapt_visual_prompt`。`passthrough=true`（Agnes 2.0）则**跳过改写**，只做长度校验。即梦 `@图片N` / `{台词}`；可灵 Omni `@image_N`（跳过首尾帧）+ `@element_N` / `对白：`。不要再写 `<<<image_N>>>`。
5. **校验失败则拆镜或降级** — 超字数、缺必填、踩禁忌、无首帧却选了 2.1 Pro：拆镜或回退 v30 / kling-v1 / Agnes 2.0。**绝不硬发**。

P2 起 `shot_runner._route_shot` 按 `video_loop` 家族接线：`ark`→Seedance，`kling` 可升 Omni/2.1，`volcengine` 仍 v30，Agnes 默认 `agnes_v25`（2.5 Flash）；`AGNES_VIDEO_MODEL=agnes-video-v2.0` 才 passthrough。

## 分页

- [seedance.md](seedance.md) — 方舟 2.5 / 2.0 Pro（`video_loop=ark`）
- [kling.md](kling.md) — Omni 3.0 / 2.1 Pro（`video_loop=kling` + 对应 MODEL 环境变量）
- [agnes.md](agnes.md) — **现行 2.5 Flash**；官方英文六段式只在附录且标明非实现

导演八步（人审顺序）三家共用，见 `docs/EVOLUTION_PLAN.md` §7。本目录只改「对模型怎么写」，不改对人怎么停。
