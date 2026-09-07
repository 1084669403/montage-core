"""server — FastAPI 看板服务。

启动：python -m montage webui --root <工作区> --port 8399
依赖：pip install "montage-core[webui]"（fastapi + uvicorn）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from montage.engine.project import init_project
from montage.engine.stages import CheckpointStore, StageStatus
from montage.registry import ToolRegistry
from montage.webui.state import (
    collect_projects,
    project_detail,
    resolve_media_file,
    resolve_produce_media,
    resolve_thumb,
    _safe_id,
)

_INDEX_HTML: str | None = None


def _load_index() -> str:
    global _INDEX_HTML
    if _INDEX_HTML is None:
        path = Path(__file__).resolve().parent / "index.html"
        _INDEX_HTML = path.read_text(encoding="utf-8")
    return _INDEX_HTML


def create_app(root: str | Path = "projects") -> "Any":
    """构建 FastAPI 应用（惰性导入，核心包无 fastapi 依赖）。"""
    try:
        from fastapi import FastAPI, HTTPException
        from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "缺少 WebUI 依赖：pip install \"montage-core[webui]\"（fastapi + uvicorn）"
        ) from exc

    root = Path(root).resolve()
    app = FastAPI(title="montage-core 看板", version="0.1.0")

    def _require_id(project_id: str) -> None:
        if not _safe_id(project_id):
            raise HTTPException(status_code=404, detail="项目不存在")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return _load_index()

    @app.get("/api/projects")
    def api_projects() -> list[dict[str, Any]]:
        return collect_projects(root)

    @app.get("/api/projects/{project_id}")
    def api_project(project_id: str) -> dict[str, Any]:
        _require_id(project_id)
        detail = project_detail(root, project_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="项目不存在")
        return detail

    @app.get("/api/projects/{project_id}/episodes/{episode_id}")
    def api_episode(project_id: str, episode_id: str) -> dict[str, Any]:
        _require_id(project_id)
        detail = project_detail(root, project_id, episode_id=episode_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="子集不存在")
        return detail

    @app.get("/api/tools")
    def api_tools() -> dict[str, Any]:
        reg = ToolRegistry()
        reg.discover()
        return {
            "catalog": reg.capability_catalog(),
            "menu": reg.provider_menu_summary(),
        }

    @app.post("/api/projects")
    def api_init(body: dict[str, Any]) -> dict[str, Any]:
        project_id = (body.get("project_id") or "").strip()
        if not project_id or not project_id.replace("-", "").replace("_", "").isalnum():
            raise HTTPException(status_code=400, detail="project_id 只能包含字母数字、-、_")
        project_dir = init_project(
            root, project_id,
            title=body.get("title") or project_id,
            pipeline_type=body.get("pipeline_type") or "cinematic",
        )
        return {"ok": True, "project_id": project_id, "dir": str(project_dir)}

    def _write_checkpoint(project_dir: Path, body: dict[str, Any]) -> dict[str, Any]:
        from montage.engine.gates import GateError, validate_completion

        store = CheckpointStore(project_dir)
        if body.get("status") == StageStatus.COMPLETED.value:
            try:
                gate = validate_completion(project_dir, body.get("stage", ""))
            except GateError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            warnings = gate["warnings"]
        else:
            warnings = []
        try:
            store.write(
                body.get("stage", ""),
                body.get("status", StageStatus.IN_PROGRESS.value),
                human_approved=bool(body.get("approved", False)),
                approved_by="human" if body.get("approved") else "",
                artifact=body.get("artifact"),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "warnings": warnings}

    @app.post("/api/projects/{project_id}/checkpoint")
    def api_checkpoint(project_id: str, body: dict[str, Any]) -> dict[str, Any]:
        _require_id(project_id)
        project_dir = root / "projects" / project_id
        if not (project_dir / "project.json").exists():
            raise HTTPException(status_code=404, detail="项目不存在")
        return _write_checkpoint(project_dir, body)

    @app.post("/api/projects/{parent_id}/episodes/{episode_id}/checkpoint")
    def api_episode_checkpoint(parent_id: str, episode_id: str, body: dict[str, Any]) -> dict[str, Any]:
        from montage.engine.episodes import EPISODE_ID_RE

        _require_id(parent_id)
        if not EPISODE_ID_RE.fullmatch(str(episode_id)):
            raise HTTPException(status_code=404, detail="子集不存在")
        project_dir = root / "projects" / parent_id / "episodes" / episode_id
        if not (project_dir / "project.json").exists():
            raise HTTPException(status_code=404, detail="子集不存在")
        return _write_checkpoint(project_dir, body)

    def _apply_review(project_dir: Path, body: dict[str, Any]) -> dict[str, Any]:
        from montage.engine.director import apply_review_fields

        fields = body.get("fields") if isinstance(body.get("fields"), list) else []
        result = apply_review_fields(project_dir, fields)
        return {"ok": True, **result}

    @app.post("/api/projects/{project_id}/review")
    def api_review(project_id: str, body: dict[str, Any]) -> dict[str, Any]:
        _require_id(project_id)
        project_dir = root / "projects" / project_id
        if not (project_dir / "project.json").exists():
            raise HTTPException(status_code=404, detail="项目不存在")
        return _apply_review(project_dir, body)

    @app.patch("/api/projects/{project_id}/review")
    def api_review_patch(project_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return api_review(project_id, body)

    @app.post("/api/projects/{parent_id}/episodes/{episode_id}/review")
    def api_episode_review(parent_id: str, episode_id: str, body: dict[str, Any]) -> dict[str, Any]:
        from montage.engine.episodes import EPISODE_ID_RE

        _require_id(parent_id)
        if not EPISODE_ID_RE.fullmatch(str(episode_id)):
            raise HTTPException(status_code=404, detail="子集不存在")
        project_dir = root / "projects" / parent_id / "episodes" / episode_id
        if not (project_dir / "project.json").exists():
            raise HTTPException(status_code=404, detail="子集不存在")
        return _apply_review(project_dir, body)

    @app.patch("/api/projects/{parent_id}/episodes/{episode_id}/review")
    def api_episode_review_patch(parent_id: str, episode_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return api_episode_review(parent_id, episode_id, body)

    def _dispatch_retry(project_dir: Path, body: dict[str, Any]) -> dict[str, Any]:
        from montage.engine.produce import run_produce

        ids = body.get("ids")
        if isinstance(ids, str):
            parsed = [p.strip() for p in ids.split(",") if p.strip()]
        elif isinstance(ids, list):
            parsed = [str(x).strip() for x in ids if str(x).strip()]
        else:
            parsed = []
        confirm = bool(body.get("confirm"))
        result = run_produce(
            project_dir,
            retry_ids=parsed,
            retry_confirmed=confirm,
            sample_hero=not confirm,
            headless=False,
        )
        if not result.get("success"):
            raise HTTPException(status_code=400, detail=str(result.get("error") or "retry 失败"))
        return result

    @app.post("/api/projects/{project_id}/retry")
    def api_retry(project_id: str, body: dict[str, Any]) -> dict[str, Any]:
        _require_id(project_id)
        project_dir = root / "projects" / project_id
        if not (project_dir / "project.json").exists():
            raise HTTPException(status_code=404, detail="项目不存在")
        return _dispatch_retry(project_dir, body)

    @app.post("/api/projects/{parent_id}/episodes/{episode_id}/retry")
    def api_episode_retry(parent_id: str, episode_id: str, body: dict[str, Any]) -> dict[str, Any]:
        from montage.engine.episodes import EPISODE_ID_RE

        _require_id(parent_id)
        if not EPISODE_ID_RE.fullmatch(str(episode_id)):
            raise HTTPException(status_code=404, detail="子集不存在")
        project_dir = root / "projects" / parent_id / "episodes" / episode_id
        if not (project_dir / "project.json").exists():
            raise HTTPException(status_code=404, detail="子集不存在")
        return _dispatch_retry(project_dir, body)

    @app.get("/media/{project_id}/renders/{filename}")
    def api_produce_media(project_id: str, filename: str) -> Any:
        _require_id(project_id)
        path = resolve_produce_media(root, project_id, filename)
        if path is None:
            raise HTTPException(status_code=404, detail="媒体不存在或路径非法")
        return FileResponse(path)

    @app.get("/media/{parent_id}/episodes/{episode_id}/renders/{filename}")
    def api_episode_produce_media(parent_id: str, episode_id: str, filename: str) -> Any:
        _require_id(parent_id)
        path = resolve_produce_media(root, parent_id, filename, episode_id=episode_id)
        if path is None:
            raise HTTPException(status_code=404, detail="媒体不存在或路径非法")
        return FileResponse(path)

    @app.get("/media/{project_id}/{filename}")
    def api_media(project_id: str, filename: str) -> Any:
        _require_id(project_id)
        path = resolve_media_file(root, project_id, filename)
        if path is None:
            raise HTTPException(status_code=404, detail="媒体不存在或路径非法")
        return FileResponse(path)

    @app.get("/api/projects/{project_id}/thumbs/{name}")
    def api_thumb(project_id: str, name: str) -> Any:
        _require_id(project_id)
        path = resolve_thumb(root, project_id, name)
        if path is None:
            raise HTTPException(status_code=404, detail="缩略图不存在")
        return FileResponse(path)

    @app.get("/api/projects/{project_id}/episodes/{episode_id}/thumbs/{name}")
    def api_episode_thumb(project_id: str, episode_id: str, name: str) -> Any:
        _require_id(project_id)
        path = resolve_thumb(root, project_id, name, episode_id=episode_id)
        if path is None:
            raise HTTPException(status_code=404, detail="缩略图不存在")
        return FileResponse(path)

    return app


def run(root: str | Path = "projects", host: str = "127.0.0.1", port: int = 8399) -> None:
    import uvicorn

    if host not in ("127.0.0.1", "localhost", "::1"):
        print(
            f"警告: WebUI 绑定 {host}，无鉴权。仅应在本机或可信网络使用。",
            flush=True,
        )
    uvicorn.run(create_app(root), host=host, port=port, log_level="info")
