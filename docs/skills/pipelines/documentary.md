# documentary（相对 cinematic 的差异）

同 7 阶段。默认：

- playbook：`documentary_restraint`
- profile：`douyin_vertical`
- edit_style：`documentary`（`edit_advisor` 克制转场）
- transition_policy：`cut_only`（assemble 把非硬切折成 cut，除非 `allow_non_cut=true`）

assets 阶段白名单多了 `asset_quality_gate`；与 cinematic 一样挂了 `shot_runner`（先 dry_run）和 `voice_director`。compose 同样有 `soundtrack_planner` / `compose_planner` / `place_audio`（纪录片 playbook `skip_bgm=true`，默认不铺 BGM）。访谈/口播保留环境声，少叠化、少 LUT 炫技。
cinematic 分阶段页仍然可对照（含字段表）；冲突时以本页与 `pipelines.py` 的 documentary 字典为准。`transition_policy=cut_only`。数字人接口在、未联调；口播/访谈走 TTS。
成片入口与 cinematic 相同：`python -m montage produce <项目目录>`（前提：已有 clip）。
