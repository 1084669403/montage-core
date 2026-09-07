# 新手上手

1. 仓库根复制 `.env.example` 为 `.env`，按注释填密钥（即梦默认要 `VOLC_ACCESSKEY` + `VOLC_SECRETKEY`）。空行等于没配。不要把 `.env` 提交 git，也不要贴到聊天。
2. `python -m montage doctor`：看 ffmpeg、jsonschema、哪些供应商 `available`。必须走这条 CLI，直接 `import` 工具不会加载 `.env`。
3. `python -m montage init <id> --title "片名" --pipeline cinematic`。
4. 按 `docs/AGENT_GUIDE.md` 的阶段顺序推进。每阶段先写 `artifacts/<产物>.json`，再 `check --completed`（门禁阶段加 `--approved`）。
5. **磁盘上已有 clip 之后**成片：`python -m montage produce <项目目录>`。单步调试才用 `python -m montage run <项目目录> <工具名> --input inputs.json`（PowerShell 不要用 `--json` 内联）。
6. 图/视频闭环写在 `proposal_packet` 的 `video_loop` / `allowed_providers`。**不要**把 `allowed_providers=["volcengine"]` 套到 TTS 或 ffmpeg。
7. 本地成片不进 7 阶段：`python -m montage auto_edit <目录> --video raw.mp4 --style documentary`。
   七阶段零 key 拼片（需 ffmpeg）：先放 clip，再 `python -m montage produce <项目目录>`（设 `MONTAGE_REAL_FFMPEG=1` 跑 `tests/test_produce_ffmpeg.py`）。

产物 schema 见 `montage/schemas.py`。执导细则见 `docs/DIRECTOR_GUIDE.md`。
