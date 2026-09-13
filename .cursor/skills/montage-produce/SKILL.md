---
name: montage-produce
description: >-
  Runs montage-core film production via python -m montage produce (拼片、成片、
  --idea 收编圣经、--retry 重跑坏镜). Use when the user wants a finished film,
  bible compile, or shot retry. Do not use for ffmpeg filter implementation
  or calling the 50 registered tools by hand.
---

# montage-produce

细节见仓库根路径 `docs/skills/meta/produce.md`。停点认 `artifacts/produce_progress.json` 的 `status` 与 `next`。执行时用**当前** `python -m montage`，只取 `next.argv` 从 `produce` 起的参数，不要照抄 `argv[0]` 的解释器路径。

## 允许

一律 `python -m montage …`：

- `init <id> --title "..."`（可加 `--pipeline`）
- `doctor`（可加 `--pipeline` / `--project` / `--json`）
- `produce <dir>` 及现有旗标：`--resume` `--idea` `--review`（`bible|none|each_episode|director`，**默认 director**） `--retry` `--yes` `--tts` `--trim-hero` `--all-video` `--profile` `--skip-finish` `--burn-subs` `--skip-export` `--strict-audio` `--keep-scratch` `--prune-exports N`（导出后只留最新 N 个 zip，默认只增不删） `--season-concat`
- 读 `artifacts/`、`artifacts/REVIEW.md`、`REVIEW.md`、`renders/`、`cost.jsonl`

真密钥默认导演八步停点。`--all-video` 与无 `--idea` 的 `--review none` 在过 `await_final_prompt` 前也会强制停一次提示词预览，不会直接烧完全片。

## 两段式

`--idea` **不编译**。没有 `script.json` / `scene_plan.json` 时不要当成分镜已定。

1. `produce <dir> --idea "..."`  
   - 无 `series_bible.json` → `status=need_bible`（只出 `format_card`）。**Agent 在此写圣经**（见下），写完再跑同一句 `--idea`。  
   - 已有圣经 → `status=await_bible`（code=0）。人改地点/人物后再跑第 2 步。
2. `produce <dir>`（**不要**带 `--idea`）→ 校验 → 编译 → 2.5 先出定妆/四视图/空镜（**不停** `await_cast`）→ 缺 clip 时默认 `await_sample`，看过样品再 `--resume`（会 **force_ids 重抽样品镜**，其余跳过已有文件）。提示词超 3000 字会停 `await_prompt`（`next.argv` 为空）：要改圣经就改完后 `produce`（不要 `--resume`）；不改则 `produce --resume` 走压缩兜底。**不要立刻 exec。**

`--idea --review none` 立刻校验+编译（`compiled`），**仍不**生成画面。不要见 `status` 就立刻再跑下一句。

## need_bible / await_bible 写什么

写入 `artifacts/series_bible.json`。地点只写地名、不写方位时，2.5 空间会扁。Python 只抄不编造地标。

- `locations[].sensory`：交叉方位散文（左/中/右 × 上/中/下 × 近处/中景/远处/更远处），可加时段/天气/声景。例：`左侧近处薄云贴地；画面中下方湿润石板；右侧远处宫殿屋脊入雾；正中更远处淡金霞光。` 没出现的格子不要写空括号。同场各镜共用，下一镜不得把「右侧远处宫殿」改到左侧。
- 关键镜 `blocking`：`x`/`z`（左/中/右 × 近/中/远），可选 `y`（上/中/下）。
- 人物 `appearance`/`outfit`/`speech_style`、对白 `speaker_id`+`text`。

`need_bible`：写完后再 `produce --idea "同一句"`。`await_bible`：改完后 `produce <dir>`，不要 `--idea`。

## 导演档（`--review director`，CLI 默认已是 director）

cinematic **推荐**八步 + 提示词总览（Agnes / 即梦）：`await_setup` → `await_outline` → `await_design` → **定妆 `await_cast`** → compile → `await_shots` → **关键帧 `await_frames`** → **最终提示词 `await_final_prompt`** → 全量后 `await_clips`。每步默认只读 `artifacts/REVIEW.md` 的「摘要」；要改字段再看「全部可改」里的选项，改对应 JSON，然后 `--resume`。人手改 REVIEW.md / review_card.json 会被覆盖。步 1～3 不写 `human_approved`。`spoken_explain` / 口播跳过定妆，design 后直接 compile。

**可灵环例外**（`video_loop=kling`）：`await_design` → compile / `await_shots` → **定妆 `await_cast`** → `await_frames` → `await_final_prompt`。工牌要先知道出场镜，不要在 shots 前出拼板。Agnes / 即梦仍按上表先 `await_cast`。

| status | 读 | 改 | 下一步 |
|--------|----|----|--------|
| `await_setup` | REVIEW 摘要（标题/时长/画幅/风格） | `series_bible.json`（title / synopsis / target_duration_seconds / playbook / environment.era / extra_notes）；画幅改 `proposal_packet.output_profile` | `--resume`（不要 `--idea`） |
| `await_outline` | 角色/地点/主题 | `series_bible.json`（characters 姓名性格关系、locations[]、music_direction、gold_lines、theme、structure、幕 narration） | `--resume` |
| `await_design` | 外观/空镜/道具 | `series_bible.json`（appearance/outfit、地点空镜、道具静物） | `--resume`（此后才全量 bible 门禁 + 定妆） |
| `await_cast` | 全身照/四视图/空镜/道具 过或失败 | 重抽：`--retry portrait/<id>`（或 `turnaround/` `scene_ref/` `prop/`）`--resume`；跳过四视图：`characters[].skip_turnaround`；修正一句：`cast_note` | 全部成功才 `--resume` compile；有失败不能进分镜 |
| `await_shots` | 各幕一行 + 总时长 | `scene_plan.json`（幕标题、景别/运镜/角度、台词、shot_budget_class、cut） | `--resume` 只出首帧；此时还没有 video clip |
| `await_frames` | n/n 成功；失败镜号 | 重抽：`--retry <shot_id> --resume`（只重抽首帧）；改该镜画面一句写 `scene_plan` | 全部成功才 `--resume` 出**最终提示词总览**（不直接进 I2V）；有失败不能进下一步 |
| `await_final_prompt` | 每镜最终图片+视频提示词（只读总览） | 只读；改提示词回 `scene_plan.json` / `series_bible.json` | `--resume` 才真正全量 I2V，然后 `await_clips` |
| `await_clips` | 成功数/失败数 | 重抽：`--retry <shot_id> --resume`（现有 retry，不要新 CLI）。可灵失败镜可写 `scene_plan.shots[].rework_mode`=`regenerate`（默认）或 `feature`（≤10s 视频参考；对白镜会丢 native 口播） | 失败已清才 `--resume` 合成 |
| `await_retry` | 费用估算；可灵二选一卡 | 可灵：`scene_plan.shots[].rework_mode` + 可选 `revision_note`。未写则 regenerate。`--retry --yes` 未写 mode 也是 regenerate。Seedance 有公网 URL 仍可自动 edit | `--resume`（不是人审） |

不要见 `next` 就立刻 exec。未知 status 或 `await_prompt` 的 `next.argv` 为空时停下来问人。

## 禁止

- Skill 里裸调 `shot_runner`（生成进 produce）
- `montage run produce`（produce 不是 BaseTool）
- 改 `edit_decisions.cuts` 的 `clip_path`
- 把 auto_edit 当七阶段成片
- 写「从想法一条命令出片」或见 next / status 就立刻 exec（`await_bible` / `await_sample` / `await_prompt` / `await_setup` / `await_outline` / `await_design` / `await_cast` / `await_shots` / `await_frames` / `await_final_prompt` / `await_clips` / `await_retry` 必须等人点头）
- 把 `await_*` 或 `approved_by=produce` 叫人审；伪造 `human_approved`
- 发明 `--autopilot`
- 见 `status=ok` 仍去拼季（未带 `--season-concat` 时不要自行加上）
