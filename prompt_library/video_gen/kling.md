# 可灵

API 面：`kling_omni_30`、`kling_t2v_30`、`kling_i2v_30`、`kling_motion_30`、`kling_i2v_21_pro`。`video_loop=kling` 锁选型到 kling，**默认 Omni**（路径即模型，不靠 `KLING_OMNI_MODEL`）。`KLING_FORCE_V1=1` 才走 v1 的 5/10 网格。

## Kling 3.0 Omni（默认车间，路径即模型）

- 中文叙事。引用：`@element_N` 点名工牌；`@image_N` **只对应 refer_image**（通常场景空镜）。first/last **不写 id、不占 @image_1**。不要 `<<<image_N>>>`、不要 `@图片N`、不要 `<Picture N>`、不要 `【剧情】`。
- 对白一行：`对白：{说话人}：{全文}`。单说话人才 `audio=native`，多说话人/无对白 `off`。
- 逐镜必须显式 `multi_shot=false`。`feature_video` 才 true，并用 `shot 1, {秒}, {正文};`；`audio=off`；时长 3–10。`base_video` 与首尾帧互斥，`audio=original`。
- 有工牌则 prompt 必须出现对应 `@element_N`，否则 valid=False。
- 时长 3–15s；视频默认 1080p；静图 Image Omni 默认 2k。水印走 `options.watermark_info.enabled=false`。
- 拼板：一张 16:9 白底格线，prompt 含该角色 appearance/outfit。道具写死纯白底。

## 母带三段式（视频运动提示词）

可灵环的视频提示词在 `build_kling_prompt` 里被重写为「母带块 + 逐镜块（+ 契约块）+ 声音块」三段式。母带从 playbook 的 `master_pattern` / `master_prompt` 注入：

- `master_pattern`：`lib/kling_master.py` 里的模式 id（`film_spectacle` / `chase_axis` / `signature_orbit` / `underwater_rescue` / `vertical_fall` / `space_dive` / `celebration_carnival`）。
- `master_prompt`：可选覆盖字符串；显式给出时覆盖模式母带句，但模式的硬约束与音频原则仍并入。

三段式结构（用 `；` 连接，整体受 `max_chars` 预算压缩，母带块优先保留）：

1. **母带块**：全局质感句（画幅/快门/胶片/调色/比例）+ 模式硬约束白/黑名单（屏幕方向、唯一慢动作、状态演进、色域/尺度/方向铁律等）+ `声音：{音频原则}`。
2. **逐镜块**：`[0s-Ns] 景别。主体动作。镜头运镜。`，保留 `@element_N` / `@image_N` / `对白：`。
3. **契约块**：由 `montage/tools/_shot_contracts.py` 按镜头序号注入——镜头类型强制（含必拍镜头与整片时段，如 `时段0-5s`）、一镜到底走位（首镜输出 beat 序列）、状态演进（全镜覆盖：切换点前输出禁止态、切换点起输出新态）、角色弧线/卡司构成/标签唯一/人群规则/声画同步/情绪曲线（首镜输出整条）、色域/尺度/方向/辉光铁律（每镜重申）。

首帧图 prompt（`still=True`）**不带母带块**——图片生成器不需要全局节奏信息。

**能力边界**：母带指令可强约束，但可灵单次生成不保证帧级/几何级精确（360° 整圈可能退化成弧线、19 风格跳变逐帧保真有限、唯一慢动作不保证变速、1s FPV 贴时长下限 3s、状态演进可能漂移、成潮人群易克隆）。返工手段见 `lib/kling_master.CAPABILITY_GUARDRAILS`，并会作为 findings 附加到 `await_final_prompt`（首轮生成前）与 `await_retry`（重抽前）两张导演确认卡。

## Kling 图生 3.0 / 文生 3.0 / 动作控制

- 图生：真同场同景别插值；主体用 `@主体名`；无 refer_image。
- 文生：无图兜底。
- 动作控制：形象交给 contents.image，正文只写动作/情绪。

## Kling 2.1 Pro

- 只走图生视频：`image` 必填，**无首帧禁选**。时长 5 或 10s。`image_tail` 尾帧。
- 中文运动描述 + `negative_prompt`。不写声音指令、不写 `{台词}` / `<<<image_N>>>`。

## v1 兜底

未切 3.0 生产路由时占位。无尾帧、无原生音频。
