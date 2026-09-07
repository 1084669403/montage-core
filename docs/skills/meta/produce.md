# produce — 已有片段拼成片

Cursor 项目 Skill 在 `.cursor/skills/montage-produce/SKILL.md`，与本页同锁。

W0：磁盘上**已经有**分镜 clip 时，一条命令按死顺序配乐 → 编译 → Ken Burns → 叠音 → 拼接 → **finish → release** → 打包。

W1：`--idea` 只收想法（级联写出 `format_card`）。无圣经 → `need_bible`（Agent 按方位写 `series_bible.json` 后再 `--idea`）；有圣经 → **停在 `await_bible`，此时还不编译**。人点头后第二次 `produce`（不要 `--idea`）才校验+编译 script/scene_plan。不是「一句话想法出片」。

W3：未标 `shot_budget_class` 的镜默认 **talk**（静图 + Ken Burns），不是 I2V。显式 `hero` 才图生视频。pending hero 时长超过全集 30% 会停（`over_hero`，不是人审）；`--trim-hero` 把超额降为 talk，`--all-video` 才全视频（成片已齐则忽略）。

W2：cinematic / documentary 且成片视频未齐时，`produce`（无 `--idea`）先 `shot_runner` dry_run，**默认只生成一镜样品**后停 `await_sample`。检查画面后 `--resume` 会 **force_ids 重抽样品镜**并生成剩余镜，可选 `--tts`，再进 W0。`--idea` 仍不生成。Agnes 2.5 提示词超 3000 字停 `await_prompt`（code=0，`next.argv` 为空）：改圣经后 `produce`（不要 `--resume`）；不改则 `--resume` 压缩再截断。导演档 `await_frames` 通过后先进 `await_final_prompt`（最终提示词总览，只读）再 `--resume` 全量视频 → `await_clips`，不再插样品。

样品停只写 `produce_progress.status=await_sample`，**不是人审**，不要写成 checkpoint `awaiting_human`。GEN+W0 成功后机器可给 `assets` / `compose` / `publish` 标 `approved_by=produce`（仍**不是人审**）；不得写 `human_approved=true`，不得自动 completed `proposal` / `script` / `scene_plan`。已有 clip 的纯 W0 不写 gated completed。

## 允许的命令

```bash
python -m montage doctor
python -m montage produce <project_dir>
python -m montage produce <project_dir> --resume
python -m montage produce <project_dir> --review none
python -m montage produce <project_dir> --review each_episode
python -m montage produce <project_dir> --idea "讲量子计算"
python -m montage produce <project_dir> --idea "..." --review bible
python -m montage produce <project_dir> --idea "..." --review director
python -m montage produce <project_dir> --tts
python -m montage produce <project_dir> --trim-hero
python -m montage produce <project_dir> --all-video
python -m montage produce <project_dir> --profile douyin_vertical
python -m montage produce <project_dir> --skip-finish
python -m montage produce <project_dir> --burn-subs
python -m montage produce <project_dir> --retry sh01,sh02
python -m montage produce <project_dir> --retry sh01 --yes
python -m montage produce <series_dir> --season-concat
```

可选：`--skip-export`、`--strict-audio`（缺 BGM 失败）、`--keep-scratch`。`--idea` 需要已有 `project.json`（`montage init`），不自动建项目。`--skip-finish` 仍会跑 release（写 `publish_log`）。`--profile` 只给 finish，不读管线 `default_profile`。`--burn-subs` 才把字幕烧进像素（默认只写 `renders/final.srt`）。

`--retry a,b` 未确认时只 dry_run，`await_retry`（code=0，`--resume` 继续，不是人审）。`--retry a,b --yes` 一趟确认：强制 GEN、关掉样品停。可灵（`video_loop=kling`）未写 `rework_mode` 默认 **regenerate**；确认卡二选一 `regenerate` / `feature`（≤10s 且需公网成片 URL；`edit` 不在默认二选一）。Seedance 有公网成片 URL 时仍优先 **edit/extend**，否则整镜重抽。系列根 / clip_factory / `await_sample` / `--idea` 时 retry 失败。成片已齐也会重跑指定镜。

`--idea` 默认 `--review director`：cascade 后依次 `await_setup` → `await_outline` → `await_design` → **定妆 `await_cast`** → compile → `await_shots` → **关键帧 `await_frames`** → **最终提示词 `await_final_prompt`**（不一次编完就 I2V；口播跳过定妆）。`--review bible` 轻量档：写出 `format_card` 后停（有圣经 → `await_bible`，**尚未** `script`/`scene_plan`）。**可灵环例外**：`await_design` → compile / `await_shots` → `await_cast` → `await_frames` → `await_final_prompt`（工牌要先知道出场镜）。`--idea --review none` 立刻校验+编译（`compiled`），仍不跑 shot_runner、不拼片。CLI 默认是 `director`。

无 `--idea` 时 `--review none` **跳过样品**、一次生成全部未齐镜，但在 `await_final_prompt` 未确认前会**强制停一次**提示词预览（先 dry_run 估算费用，再构建提示词停卡）；`--resume` 才全量。**系列根例外**：系列子集（`run_series_produce`）的 `--review none` 保持无人值守直跑，不强制停（W6 批量成片通道）。扁平项目上 `--review bible` / `each_episode` **忽略**（只在 `--idea` 有含义的是 bible；只影响样品的是 `none`）。

## 多集

仅当 `artifacts/episodes.json` 的 `episodes.length>=2` 且 `project.json` 没有 `parent_id`/`episode_id` 时，当前目录是**系列根**。produce 会把每集物化成完整子项目：

```text
projects/<id>/
  artifacts/series_bible.json
  artifacts/episodes.json
  episodes/ep01/   # 自有 project.json / artifacts / renders
  episodes/ep02/
  episodes/ep03/
```

单集保持扁平目录，不建 `episodes/`。

```bash
python -m montage produce <series_dir>                 # 默认跑未完成的一集，样品停 await_sample
python -m montage produce <series_dir> --resume         # await_sample 续本集；本集 ok 后 await_episode，再 --resume 下一集
python -m montage produce <series_dir> --review none    # 连跑未完成集，每集跳过样品
python -m montage produce <series_dir> --season-concat  # 全集 ok 后才写 renders/season.mp4（不是 final.mp4）
python -m montage produce <series_dir>/episodes/ep02    # 只动这一集，不碰系列根进度
```

`await_episode` **不是人审**。BGM 仍每集一首。默认不拼季；仅 `--season-concat` 且全集 ok 才写系列根 `renders/season.mp4`。扁平项目与 `--idea` 带该旗标会忽略。`--idea` 仍只写当前目录圣经，不物化、不 GEN。空 `scene_ids` 的集跳过，不会把全集写入该集。

无 bible 时只写 `format_card` 骨架，不编假剧情。

## 前提

成片（无 `--idea`）：

- clip **已经齐**（每镜有对应 `kind=video`，静图镜有静图）：直接 W0 拼片。
- cinematic / documentary 且成片未齐：要有 `project.json` + `artifacts/scene_plan.json`；不要求已有 `asset_manifest`。会先估算，再出样品（或 `--review none` 全量）。
- `clip_factory` 缺 clip 仍失败（不跑 shot_runner）。

允许手写或复制 fixture JSON 与媒体文件来准备项目。

## 禁止

- Skill 里裸调 `shot_runner`（生成进 produce）
- `montage run produce`（produce 不是 BaseTool）
- 把机器通过写成 `human_approved=true`
- 把 `approved_by=produce` 或样品停叫作人审
- 文档或 Skill 写「produce 从想法一条命令出片」
- `--idea` 路径跑配乐/拼接/生成
- 无 `--season-concat` 时在系列根写出整季成片（默认不拼；旗标产物是 `renders/season.mp4`，不是 `final.mp4`）
- 往 `assets/bgm/INDEX.md` 追加远程歌。默认整集一首 BGM；仅编译进 scene_plan 的 `bgm_id` 才按场多轨（禁止用情绪词猜歌）

## 顺序

成片（clip 已齐）：`soundtrack_planner resolve=true` → `compose_planner overwrite=true` → `realize`（有静图）→ `place_audio`（有 soundtrack 事件，仅 BGM 也要跑）→ `ffmpeg_compose assemble` → **finish**（显式 LUT / `--profile` 或提案 `output_profile` / 仅 `script.title` 的 2s 片头+lower-third / SRT 旁路）→ **release**（`release_pack`：封面 + 简介模板 + `publish_log=exported`）→ `export_bundle` 到项目 `exports/`。

`--idea` 路径不跑 finish / release / export。片头不回落 `project.json.title`。finish 中间 mp4 只进 `scratch/`，成功后只替换 `renders/final.mp4`。

缺 clip（cinematic/documentary）：bible 路径且 Agnes 2.5 时先 `stage=cast`（定妆/四视图/空镜，写入公网 URL；**不停** `await_cast`）→ `shot_dry_run`（`record_ledger=false`，估全片剩余，不传 `max_shots`）→ 样品 `shot_generate`（`retry_ids=[sample_id]`，第一镜 `hero` 否则时间线第一镜）→ `await_sample`。`--resume`：全量 generate（不传 `retry_ids`，`force_ids=[sample_id]` 重抽样品镜）→ 可选 `voice`（`--tts`）→ 再进上一段。导演档 `await_frames` 通过后**不插** `await_sample`，先进 `await_final_prompt`（只读提示词总览，`--resume` 才全量 I2V）→ `await_clips` 再 W0。`--review none` 无 `--idea` 时在全量前**强制停** `await_final_prompt`（先 dry_run 后构建提示词停卡）。有人物的 hero 缺定妆 URL 会失败，不降级 `mode=text`。提示词超 3000 字停 `await_prompt`，不默默截断。

`--idea`：`cascade` → 无圣经则 `need_bible`；有圣经且 `--review bible` 则 `await_bible`（不 validate、不 compile）。第二次无 `--idea` 的 `produce` 才 `validate_bible` → `compile`（抄 `locations[].sensory`）。`--idea --review none` 仍立刻校验+编译。`--review director` 分步停，Agnes/即梦 design 后先定妆再 compile，shots 后先首帧；可灵环 compile/`await_shots` 后再定妆。精修改 `series_bible.json` 再编，手改 scene_plan 会被覆盖。导演档 design 之前不跑全量 bible 门禁。地点 `sensory` 用交叉方位，只写地名则 2.5 空间扁。

真密钥机器：默认先出样品再 `--resume`；CI mock 不挡。

## 生图供应商选择（image_providers）

`artifacts/proposal_packet.json` 的 `image_providers`（list[str]）只锁定**生图**（定妆/道具/首帧），`allowed_providers`/`video_loop` 只锁定视频，两者解耦：

- 配了 `image_providers`（如 `["ark"]`）：生图按配置执行，**不再询问**。ark 环走 `seedream_image`（Seedream 5.0 lite，模型写死 lite 家族禁止降级，参考图装箱失败硬报错不转纯文生，¥0.22/张）。huapi_liaozhai 已配 `["ark"]`，生图即 Seedream、视频继续可灵烧额度
- 未配置：保持现状（图片默认 kling）；**Agent 在 produce 生图前先询问用户用哪个供应商**（可灵 / Seedream / 即梦），得到答复后写 `image_providers` 再继续，不要替用户默认
- `inputs.image_providers` 显式传入时优先级最高（同 inputs.allowed_providers 语义）

## 进度 `next` 与无人值守

终态的 `artifacts/produce_progress.json` 带 `next.argv`（本机 `sys.executable` + `-m montage produce …`）。执行用**当前** `python -m montage`，只取从 `produce` 起的参数，不要照抄 `argv[0]`。

- `need_bible`：`argv` 为空。按 Skill 写 `series_bible.json`（地点 `sensory` 交叉方位），再 `produce --idea`
- `await_bible`：无 `--idea`、无 `--review none`（尚未编译；改完 `series_bible.json` 再跑）
- `await_prompt`：`argv` 为空。要改圣经就改完后 `produce`（不要 `--resume`）；不改则 `produce --resume` 走压缩兜底。Skill 禁止立刻 exec
- `await_setup` / `await_outline` / `await_design` / `await_cast` / `await_shots` / `await_frames` / `await_final_prompt` / `await_clips`：只加 `--resume`（导演档；先读 `artifacts/REVIEW.md` 摘要，改 JSON，不要 `--idea`）。`await_cast` 有失败则 `--retry portrait/<id>`（或 `turnaround/` / `scene_ref/` / `prop/`）后再 `--resume`。`await_frames` / `await_clips` 有失败则 `--retry <shot_id> --resume`。`await_final_prompt` 只读预览，改提示词回 `scene_plan` / `series_bible`，`--resume` 才全量 I2V。可灵 `await_retry` / 失败镜可写 `scene_plan.shots[].rework_mode`（`regenerate` 默认 / `feature` ≤10s）。`await_frames` 通过后先进 `await_final_prompt`，不再插 `await_sample`
- `compiled`：无 `--idea`、有 `--review none`（已经跳过 bible 停，可以立刻跑）
- `await_sample` / `await_retry` / `await_episode`：只加 `--resume`（样品/retry 等人点头）
- `ok`：`argv` 为空

`MONTAGE_HEADLESS=1`（或 `true`/`yes`）在生成 / dry_run **之前**拒绝会停在样品、`await_prompt` 或未确认 retry 的路径，status=`fail`（code=2），不自动改成 `--review none`。`next` 补救：样品/系列 → `--review none`；未确认 retry → `--resume`。去掉该环境变量后仍可交互出样品再 `--resume`。看板 retry 始终非 Headless。
