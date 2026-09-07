# 导演技能索引

> 给 Agent / 人类导演的操作页。代码只提供工具与门禁；创意决策写在这里。
> **不要照抄第三方技能库原文**。管线配置以 `montage/pipelines.py` 为准。

## 入门

- [新手上手](meta/onboarding.md) — 从 `montage init` 到第一份 checkpoint
- [成片 produce](meta/produce.md) — 已有 clip 拼片；缺 clip 时 cinematic/documentary 会先生成
- [参考片怎么用](meta/reference-video.md) — 只借鉴结构与节奏，不复刻画面

## cinematic（7 阶段各一页）

1. [research](pipelines/cinematic/research.md)
2. [proposal](pipelines/cinematic/proposal.md)
3. [script](pipelines/cinematic/script.md)
4. [scene_plan](pipelines/cinematic/scene_plan.md)
5. [assets](pipelines/cinematic/assets.md)
6. [compose](pipelines/cinematic/compose.md)
7. [publish](pipelines/cinematic/publish.md)

## 其他管线（相对 cinematic 的差异）

- [documentary](pipelines/documentary.md) — 克制硬切、竖屏、`documentary_restraint`
- [clip_factory](pipelines/clip_factory.md) — 导演写 `clip_plan`，不要用 scene_detect 自动当剧本

## 命令

```bash
python -m montage doctor --pipeline cinematic
python -m montage doctor --project <project_dir>
python -m montage produce <project_dir>
python -m montage produce <project_dir> --resume
python -m montage produce <project_dir> --idea "讲量子计算"
python -m montage produce <project_dir> --retry sh01,sh02
python -m montage produce <project_dir> --retry sh01 --yes
python -m montage produce <project_dir> --trim-hero
python -m montage produce <project_dir> --all-video
python -m montage produce <series_dir> --review each_episode
python -m montage produce <series_dir>/episodes/ep02 --resume
```

成片主路径是 `produce`。已有 clip 直接拼片；cinematic/documentary 缺成片视频时会先 dry_run，默认出一镜样品后停，`--resume` 再全量。`--idea` 默认导演档先停 `await_setup`（尚未编译）；`--review bible` 才停 `await_bible`；无圣经则 `need_bible`，写完再 `--idea`。详见 [produce](meta/produce.md)。
