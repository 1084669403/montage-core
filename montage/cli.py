"""cli — 命令行入口。

命令：
  montage doctor [--pipeline NAME] [--project DIR] [--json]
  montage tools
  montage init <id> --title "..." [--root DIR]
  montage status <project_dir>
  montage check <project_dir> <stage> --completed --approved [--caps ...] [--caps-strict]
  montage run <project_dir> <tool> --input inputs.json
  montage produce <project_dir> [--resume] [--skip-export] [--strict-audio] [--keep-scratch] [--tts]
  montage produce <project_dir> --idea "讲量子计算" [--review bible]
  montage auto_edit <project_dir> --video/--clips/--audio-only --style ...
  montage webui [--port 8399]

本文件为全新原创代码。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from montage.engine.project import init_project
from montage.engine.stages import CheckpointStore, StageStatus, STAGE_ORDER
from montage.registry import ToolRegistry


def _discover() -> ToolRegistry:
    reg = ToolRegistry()
    reg.discover()
    return reg


def _doctor_payload(reg: ToolRegistry, args: argparse.Namespace) -> dict:
    summary = reg.provider_menu_summary()
    try:
        import jsonschema  # noqa: F401
        has_schema = True
    except ImportError:
        has_schema = False
    from montage.engine.envfile import injected_key_count
    from montage.providers.capabilities import doctor_video_surfaces

    payload: dict = {
        "capabilities": summary["capabilities"],
        "setup_offers": summary.get("setup_offers") or [],
        "import_errors": summary.get("import_errors") or [],
        "video_surfaces": doctor_video_surfaces(),
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "jsonschema": has_schema,
        "dotenv_injected": injected_key_count(),
    }
    if getattr(args, "pipeline", None) or getattr(args, "project", None):
        from montage.engine.policy import skill_paths_for_pipeline, skill_paths_for_project

        payload["skills"] = (
            skill_paths_for_project(args.project)
            if args.project
            else skill_paths_for_pipeline(args.pipeline)
        )
    return payload


def _format_duration_policy(policy: dict) -> str:
    kind = str((policy or {}).get("kind") or "")
    if kind == "enum":
        values = (policy or {}).get("values") or []
        return "/".join(str(v) + "s" for v in values) or "enum"
    if kind == "range":
        return f"{policy.get('min')}–{policy.get('max')}s"
    if kind == "none":
        return "none"
    return kind or "-"


def _cmd_doctor(reg: ToolRegistry, args: argparse.Namespace) -> int:
    payload = _doctor_payload(reg, args)
    if getattr(args, "as_json", False):
        print(json.dumps(payload, ensure_ascii=False, default=str))
        return 0
    print("== 工具能力菜单 ==")
    for cap in payload["capabilities"]:
        mark = "ok" if cap["configured"] else "--"
        print(f"  {mark} {cap['capability']}: {cap['configured']}/{cap['total']}")
        for tool in cap["tools"]:
            print(f"      - {tool['name']} ({tool['provider']}) [{tool['status']}]")
    for offer in payload["setup_offers"]:
        print(f"  待配置: {offer['tool']} → {offer['hint']}")
    print("== 视频 API 面 ==")
    for surface in payload.get("video_surfaces") or []:
        wired = "接线" if surface.get("wired") else "未接线"
        audio = "audio=yes" if surface.get("native_audio") else "audio=no"
        multi = "multi=yes" if surface.get("multi_shot") else "multi=no"
        policy = surface.get("duration_policy") or {}
        dur = _format_duration_policy(policy)
        print(
            f"  {surface.get('api_id')}  {surface.get('label')}  [{wired}]  "
            f"{audio}  {multi}  {dur}"
        )
    errors = payload.get("import_errors") or []
    if errors:
        print("== 发现时导入失败 ==")
        for item in errors:
            print(f"  {item}")
    print(f"ffmpeg: {'OK' if payload['ffmpeg'] else 'MISSING'}")
    print(f"jsonschema: {'OK' if payload['jsonschema'] else 'MISSING（核心依赖，pip install -e .）'}")
    print(f"dotenv: 已从 .env 注入 {payload['dotenv_injected']} 个键")
    skills = payload.get("skills") or []
    if skills:
        print("== 导演技能 ==")
        for p in skills:
            print(f"  {p}")
    return 0


def _cmd_tools(reg: ToolRegistry) -> int:
    for cap, names in reg.capability_catalog().items():
        print(f"[{cap}]")
        for name in names:
            print(f"  {name}")
    return 0


def _cmd_init(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    proj = init_project(root, args.project_id, args.title, args.pipeline)
    print(f"项目已初始化: {proj}")
    print(f"  project.json  {proj / 'project.json'}")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    store = CheckpointStore(Path(args.project_dir))
    for stage in STAGE_ORDER:
        cp = store.read(stage)
        status = cp.status if cp else StageStatus.PENDING.value
        mark = {"completed": "✓", "in_progress": "◐", "awaiting_human": "⏸"}.get(status, "·")
        print(f"  {mark} {stage:<12} {status}")
    nxt = store.next_stage()
    print(f"\n下一阶段: {nxt or '（全部完成）'}")
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    from montage.engine.gates import GateError, check_tool_allowlist, validate_completion

    store = CheckpointStore(Path(args.project_dir))

    # 产物门禁：completed 前校验 produces 产物存在且过 schema（只拦 completed）
    if args.completed:
        try:
            gate = validate_completion(args.project_dir, args.stage)
        except GateError as exc:
            print(f"错误: {exc}", file=sys.stderr)
            return 2
        for warn in gate["warnings"]:
            print(f"提示: {warn}")

    # 工具白名单：--caps 使用的能力族须在阶段允许范围内；--caps-strict 则失败
    if args.caps:
        warns = check_tool_allowlist(
            args.stage, [c.strip() for c in args.caps.split(",") if c.strip()],
            project_dir=args.project_dir,
        )
        for warn in warns:
            print(f"提示: {warn}")
        if args.caps_strict and warns:
            print("错误: 能力族白名单校验失败（--caps-strict）", file=sys.stderr)
            return 2

    try:
        store.write(
            args.stage,
            StageStatus.COMPLETED.value if args.completed else StageStatus.IN_PROGRESS.value,
            human_approved=args.approved,
            approved_by="human" if args.approved else "",
            artifact=args.artifact,
        )
        print(f"{args.stage}: checkpoint 已写入")
        return 0
    except ValueError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2


def _cmd_auto_edit(args: argparse.Namespace) -> int:
    """快速路径：plan → 可选 replan → preview/render。不调用 init_project。"""
    from montage.style_packs import STYLE_PACKS, list_style_packs
    from montage.tools.auto_edit import AutoEdit

    if args.overrides:
        try:
            overrides = json.loads(args.overrides)
        except json.JSONDecodeError as exc:
            print(f"错误: --overrides 不是合法 JSON（{exc}）", file=sys.stderr)
            return 2
        if not isinstance(overrides, dict):
            print("错误: --overrides 必须是 JSON 对象", file=sys.stderr)
            return 2
    else:
        overrides = {}

    if args.style not in STYLE_PACKS:
        known = ", ".join(p["id"] for p in list_style_packs())
        print(f"错误: 未知风格包 {args.style!r}（可选: {known}）", file=sys.stderr)
        return 2

    present = sum(bool(x) for x in (args.video, args.clips, args.audio_only))
    if present != 1:
        print("错误: --video / --clips / --audio-only 必须三选一", file=sys.stderr)
        return 2

    tool = AutoEdit()
    project_dir = str(Path(args.project_dir))
    base: dict = {
        "project_dir": project_dir,
        "style": args.style,
        "has_bgm": bool(args.has_bgm),
        "is_speech": bool(args.is_speech),
        "language": args.language,
        "resume": not args.no_resume,
    }
    if args.video:
        base["video"] = args.video
    elif args.clips:
        base["clips"] = list(args.clips)
    else:
        base["audio_only"] = args.audio_only
    if args.profile:
        base["profile"] = args.profile
    if args.target_duration is not None:
        base["target_duration"] = args.target_duration

    pack_keys = {"lut", "transitions", "pacing", "output_profile", "bind_playbook"}
    pack_over = {k: overrides[k] for k in pack_keys if k in overrides}
    replan_over = {k: v for k, v in overrides.items() if k not in pack_keys}

    plan_inputs = dict(base)
    plan_inputs["operation"] = "plan"
    if pack_over:
        plan_inputs["overrides"] = pack_over
    result = tool.execute(plan_inputs)
    if not result.success:
        print(f"错误: plan 失败 — {result.error}", file=sys.stderr)
        return 2
    print(f"plan: {result.data.get('path')}")

    if replan_over:
        r2 = tool.execute({
            "operation": "replan",
            "project_dir": project_dir,
            "overrides": replan_over,
        })
        if not r2.success:
            print(f"错误: replan 失败 — {r2.error}", file=sys.stderr)
            return 2
        print(f"replan: changed={r2.data.get('changed')}")

    render_inputs = dict(base)
    render_inputs["operation"] = "preview" if args.preview else "render"
    rendered = tool.execute(render_inputs)
    if not rendered.success:
        print(f"错误: {render_inputs['operation']} 失败 — {rendered.error}", file=sys.stderr)
        return 2
    print(f"output: {rendered.data.get('output_path')}")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    """跑单个工具：stdout 一行 JSON（ToolResult），路径保持相对 cwd。"""
    from montage.engine.runtime import run_tool

    if args.input_path:
        try:
            payload = json.loads(Path(args.input_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"错误: 无法读取 --input（{exc}）", file=sys.stderr)
            return 2
    elif args.json_inline:
        try:
            payload = json.loads(args.json_inline)
        except json.JSONDecodeError as exc:
            print(f"错误: --json 不是合法 JSON（{exc}）", file=sys.stderr)
            return 2
    else:
        payload = {}
    if not isinstance(payload, dict):
        print("错误: 输入必须是 JSON 对象", file=sys.stderr)
        return 2
    payload["project_dir"] = str(Path(args.project_dir))
    reg = _discover()
    cls = reg.get(args.tool)
    if cls is None:
        print(f"错误: 未知工具 {args.tool!r}", file=sys.stderr)
        return 2
    result = run_tool(cls(), payload)
    print(json.dumps(
        {
            "success": result.success,
            "data": result.data,
            "error": result.error,
            "meta": result.meta,
            "cost_usd": result.cost_usd,
        },
        ensure_ascii=False,
        default=str,
    ))
    return 0 if result.success else 2


def _print_next(progress: dict | None) -> None:
    nxt = (progress or {}).get("next") if isinstance(progress, dict) else None
    if not isinstance(nxt, dict):
        return
    argv = nxt.get("argv") or []
    if not isinstance(argv, list) or "produce" not in argv:
        return
    idx = argv.index("produce")
    print("next: python -m montage " + " ".join(str(x) for x in argv[idx:]))


def _cmd_produce(args: argparse.Namespace) -> int:
    from montage.engine.produce import run_produce

    result = run_produce(
        args.project_dir,
        resume=bool(args.resume),
        skip_export=bool(args.skip_export),
        strict_audio=bool(args.strict_audio),
        keep_scratch=bool(args.keep_scratch),
        idea=getattr(args, "idea", None),
        review=str(getattr(args, "review", None) or "director"),
        tts=bool(getattr(args, "tts", False)),
        sample_hero=not (
            not getattr(args, "idea", None)
            and str(getattr(args, "review", None) or "director") == "none"
        ),
        trim_hero=bool(getattr(args, "trim_hero", False)),
        all_video=bool(getattr(args, "all_video", False)),
        skip_finish=bool(getattr(args, "skip_finish", False)),
        burn_subs=bool(getattr(args, "burn_subs", False)),
        profile=str(getattr(args, "profile", None) or ""),
        retry_ids=None if getattr(args, "retry", None) is None else [
            p.strip() for p in str(args.retry).split(",") if p.strip()
        ],
        retry_confirmed=bool(getattr(args, "yes", False)),
        season_concat=bool(getattr(args, "season_concat", False)),
    )
    status = str((result.get("progress") or {}).get("status") or "")
    if result.get("success"):
        director_msg = {
            "await_setup": "produce: await_setup（确认需求卡后 --resume；改 series_bible.json，不要改 REVIEW.md）",
            "await_outline": "produce: await_outline（确认大纲后 --resume）",
            "await_design": "produce: await_design（确认设计表后 --resume；此后才定妆）",
            "await_cast": "produce: await_cast（确认全身照/四视图后 --resume；失败则 --retry <id> --resume）",
            "await_shots": "produce: await_shots（确认分镜后 --resume；此后才出关键帧）",
            "await_frames": "produce: await_frames（确认首帧后 --resume；失败则 --retry <id> --resume）",
            "await_final_prompt": "produce: await_final_prompt（确认最终提示词总览后 --resume 才生成；改提示词回 scene_plan / series_bible）",
            "await_clips": "produce: await_clips（确认单镜视频后 --resume；失败则 --retry <id> --resume）",
        }
        if status in director_msg:
            print(director_msg[status])
        elif status == "await_bible":
            print("produce: await_bible（改完 series_bible.json 后再 produce，不要 --idea）")
        elif status == "await_prompt":
            print("produce: await_prompt（提示词超 3000 字。要改圣经就改完后 produce，不要 --resume；不改则 --resume 走压缩兜底）")
        elif status == "await_sample":
            print("produce: await_sample（检查样品后 --resume 继续；这不是人审）")
        elif status == "await_retry":
            print("produce: await_retry（检查费用后 --resume；可灵可改 scene_plan.shots[].rework_mode，未写则 regenerate；这不是人审）")
        elif status == "await_episode":
            print("produce: await_episode（本集完成，--resume 下一集；这不是人审）")
        else:
            print("produce: ok")
        _print_next(result.get("progress") if isinstance(result.get("progress"), dict) else None)
        return 0
    if status == "over_hero":
        print(f"错误: {result.get('error') or 'pending hero 超过 30%'}（--trim-hero 或 --all-video）", file=sys.stderr)
        _print_next(result.get("progress") if isinstance(result.get("progress"), dict) else None)
        return int(result.get("code") or 2)
    print(f"错误: {result.get('error') or 'produce 失败'}", file=sys.stderr)
    _print_next(result.get("progress") if isinstance(result.get("progress"), dict) else None)
    return int(result.get("code") or 2)


def _cmd_webui(args: argparse.Namespace) -> int:
    try:
        from montage.webui.server import run
    except ImportError:
        print("缺少 WebUI 依赖：pip install \"montage-core[webui]\"", file=sys.stderr)
        return 2
    if str(args.host) not in ("127.0.0.1", "localhost", "::1"):
        print(f"警告: --host {args.host} 无鉴权，仅应在本机或可信网络使用。", file=sys.stderr)
    print(f"看板启动: http://{args.host}:{args.port}  (root={Path(args.root).resolve()})")
    run(Path(args.root), host=args.host, port=args.port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="montage", description="montage-core CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_doctor = sub.add_parser("doctor", help="环境自检")
    p_doctor.add_argument("--pipeline", default=None, help="打印该管线导演技能路径")
    p_doctor.add_argument("--project", default=None, help="按项目 pipeline_type 打印技能路径")
    p_doctor.add_argument("--json", dest="as_json", action="store_true", help="一行 JSON（不含密钥值）")
    sub.add_parser("tools", help="列出工具")

    p_init = sub.add_parser("init", help="初始化项目工作区")
    p_init.add_argument("project_id")
    p_init.add_argument("--title", default="untitled")
    p_init.add_argument("--pipeline", default="cinematic")
    p_init.add_argument("--root", default=str(Path.cwd()))

    p_status = sub.add_parser("status", help="查看阶段状态")
    p_status.add_argument("project_dir")

    p_check = sub.add_parser("check", help="写 checkpoint")
    p_check.add_argument("project_dir")
    p_check.add_argument("stage", choices=STAGE_ORDER)
    p_check.add_argument("--completed", action="store_true")
    p_check.add_argument("--approved", action="store_true", help="人类已审批（门禁阶段必填）")
    p_check.add_argument("--artifact", default=None)
    p_check.add_argument("--caps", default=None, help="本次使用的能力族列表（逗号分隔），越权则告警，如 analysis,prompt_engineering")
    p_check.add_argument("--caps-strict", dest="caps_strict", action="store_true", help="能力族越权时失败（默认仅告警）")

    p_run = sub.add_parser("run", help="执行已注册工具（--input JSON 文件）")
    p_run.add_argument("project_dir")
    p_run.add_argument("tool")
    p_run.add_argument("--input", dest="input_path", default=None, help="inputs.json（Windows 推荐）")
    p_run.add_argument("--json", dest="json_inline", default=None, help="内联 JSON（PowerShell 易踩坑，优先用 --input）")

    p_ae = sub.add_parser("auto_edit", help="AutoEditor 快速路径（不进 7 阶段管线）")
    p_ae.add_argument("project_dir", help="项目目录（缺 project.json 时在该目录原地写入，不调用 init）")
    src = p_ae.add_mutually_exclusive_group(required=True)
    src.add_argument("--video", help="单个视频文件")
    src.add_argument("--clips", nargs="+", help="多段视频（顺序拼接，段内独立分析）")
    src.add_argument("--audio-only", dest="audio_only", help="仅音频（黑场占位 + 混入音轨）")
    p_ae.add_argument("--style", default="documentary", help="风格包 id（默认 documentary）")
    p_ae.add_argument("--profile", default=None, help="覆盖 output_profile（如 douyin_vertical）")
    p_ae.add_argument("--overrides", default=None, help="JSON 对象：StylePack 覆写和/或 replan（段/镜头）")
    p_ae.add_argument("--has-bgm", dest="has_bgm", action="store_true")
    p_ae.add_argument("--is-speech", dest="is_speech", action="store_true")
    p_ae.add_argument("--language", default="zh")
    p_ae.add_argument("--target-duration", dest="target_duration", type=float, default=None)
    p_ae.add_argument("--preview", action="store_true", help="只出 480p 预览到 tmp_autoedit/preview.mp4")
    p_ae.add_argument("--no-resume", dest="no_resume", action="store_true", help="忽略中间产物，全量重渲")

    p_prod = sub.add_parser("produce", help="已有分镜片段拼片，或 --idea 收编圣经")
    p_prod.add_argument("project_dir")
    p_prod.add_argument("--resume", action="store_true", help="只跑未成功的步骤")
    p_prod.add_argument("--skip-export", dest="skip_export", action="store_true")
    p_prod.add_argument("--strict-audio", dest="strict_audio", action="store_true", help="缺 BGM 则失败")
    p_prod.add_argument("--keep-scratch", dest="keep_scratch", action="store_true", help="保留中间文件")
    p_prod.add_argument("--idea", default=None, help="W1：级联分析并收编圣经（不生成、不拼片）")
    p_prod.add_argument("--tts", action="store_true", help="W2：生成后合成对白（默认不合成）")
    p_prod.add_argument("--trim-hero", dest="trim_hero", action="store_true", help="W3：把超额 pending hero 降为 talk")
    p_prod.add_argument("--all-video", dest="all_video", action="store_true", help="W3：全镜 I2V，跳过 30%（成片已齐则忽略）")
    p_prod.add_argument("--skip-finish", dest="skip_finish", action="store_true", help="跳过 LUT/profile/片头（release 仍跑）")
    p_prod.add_argument("--burn-subs", dest="burn_subs", action="store_true", help="finish 把字幕烧进像素（默认只写 SRT 旁路）")
    p_prod.add_argument("--profile", default="", help="finish 显式平台档案；不读管线 default_profile")
    p_prod.add_argument("--retry", default=None, help="重跑镜 id，逗号分隔；未 --yes/--resume 则 await_retry")
    p_prod.add_argument("--yes", action="store_true", help="确认 retry（只对此生效）")
    p_prod.add_argument(
        "--season-concat",
        dest="season_concat",
        action="store_true",
        help="系列根：全集完成后拼接 renders/season.mp4（默认不拼）",
    )
    p_prod.add_argument(
        "--review",
        default="director",
        choices=["bible", "none", "each_episode", "director"],
        help="--idea 时 director 走八步停点；bible 轻量停；无 --idea 时 none 跳过样品；系列根 bible/each_episode 按集停。默认 director",
    )

    p_webui = sub.add_parser("webui", help="启动 Web 看板")
    p_webui.add_argument("--host", default="127.0.0.1")
    p_webui.add_argument("--port", type=int, default=8399)
    p_webui.add_argument("--root", default=str(Path.cwd()))
    return parser


def main(argv: list[str] | None = None) -> int:
    from montage.engine.envfile import load_envfile

    load_envfile()
    args = build_parser().parse_args(argv)
    if args.cmd == "doctor":
        return _cmd_doctor(_discover(), args)
    if args.cmd == "tools":
        return _cmd_tools(_discover())
    if args.cmd == "init":
        return _cmd_init(args)
    if args.cmd == "status":
        return _cmd_status(args)
    if args.cmd == "check":
        return _cmd_check(args)
    if args.cmd == "run":
        return _cmd_run(args)
    if args.cmd == "auto_edit":
        return _cmd_auto_edit(args)
    if args.cmd == "produce":
        return _cmd_produce(args)
    if args.cmd == "webui":
        return _cmd_webui(args)
    return 1
