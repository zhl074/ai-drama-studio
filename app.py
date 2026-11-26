"""AI 多模态创作与短剧生成 Agent 平台 —— FastAPI 主服务"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import agents
import config
import db
import providers

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
for noisy in ("httpx2", "httpx", "httpcore2", "openai"):
    logging.getLogger(noisy).setLevel(logging.WARNING)
logger = logging.getLogger("studio")

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

db.init_db()


class UTF8JSONResponse(JSONResponse):
    """显式声明 charset，避免某些客户端（如 PowerShell）按 Latin-1 误解码中文"""
    media_type = "application/json; charset=utf-8"


app = FastAPI(
    title="AI 短剧生成 Agent 平台",
    version="1.0.0",
    default_response_class=UTF8JSONResponse,
)
app.mount("/media", StaticFiles(directory=str(config.MEDIA_DIR)), name="media")


# ---------------------------------------------------------------- 模型
class CreateProjectReq(BaseModel):
    idea: str = Field(..., min_length=2, max_length=600)


class CreateEpisodeReq(BaseModel):
    ep_index: int = Field(default=1, ge=1, le=50)


class RenderReq(BaseModel):
    """渲染参数：可临时覆盖生成能力"""
    image_provider: str | None = None
    tts_provider: str | None = None
    video_mode: str | None = None      # kenburns / still / t2v


def sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


# ---------------------------------------------------------------- 页面 & 健康
@app.get("/")
async def index():
    page = STATIC_DIR / "index.html"
    if not page.exists():
        raise HTTPException(404, "前端页面缺失")
    return FileResponse(page)


@app.get("/api/health")
async def health():
    return {
        "status": "ok" if config.API_KEY else "no_api_key",
        "llm_model": config.LLM_MODEL,
        "allow_paid": config.ALLOW_PAID,
        "image_provider": config.IMAGE_PROVIDER,
        "image_model": config.IMAGE_MODEL,
        "tts_provider": config.TTS_PROVIDER,
        "video_mode": config.VIDEO_MODE,
        "t2v_model": config.T2V_MODEL,
        "t2v_max_shots": config.T2V_MAX_SHOTS,
        "ffmpeg": Path(providers.ffmpeg_exe()).name,
        "video": f"{config.VIDEO_WIDTH}x{config.VIDEO_HEIGHT}@{config.VIDEO_FPS}",
        "projects": len(db.list_projects()),
    }


# ---------------------------------------------------------------- 项目
@app.get("/api/projects")
async def list_projects():
    return {"projects": db.list_projects()}


@app.post("/api/projects")
async def create_project(req: CreateProjectReq):
    """① 策划 Agent：创意 → 剧名/梗概/角色/大纲"""
    try:
        project = await asyncio.to_thread(agents.run_concept, req.idea)
    except Exception as e:  # noqa: BLE001
        logger.exception("策划失败")
        raise HTTPException(500, f"策划失败：{e}") from e
    return {"project": project}


@app.get("/api/projects/{pid}")
async def get_project(pid: str):
    p = db.get_project(pid)
    if not p:
        raise HTTPException(404, "项目不存在")
    return {"project": p, "episodes": db.list_episodes(pid)}


@app.delete("/api/projects/{pid}")
async def delete_project(pid: str):
    ok = db.delete_project(pid)
    if not ok:
        raise HTTPException(404, "项目不存在")
    return {"ok": True}


# ---------------------------------------------------------------- 剧集
@app.post("/api/projects/{pid}/episodes")
async def create_episode(pid: str, req: CreateEpisodeReq):
    """② 编剧 Agent + ③ 分镜 Agent"""
    project = db.get_project(pid)
    if not project:
        raise HTTPException(404, "项目不存在")
    try:
        ep = await asyncio.to_thread(agents.create_episode, project, req.ep_index)
    except Exception as e:  # noqa: BLE001
        logger.exception("剧本生成失败")
        raise HTTPException(500, f"剧本生成失败：{e}") from e
    return {"episode": ep}


@app.get("/api/episodes/{eid}")
async def get_episode(eid: str):
    ep = db.get_episode(eid)
    if not ep:
        raise HTTPException(404, "剧集不存在")
    return {"episode": ep}


@app.post("/api/episodes/{eid}/render")
async def render(eid: str, req: RenderReq | None = None):
    """
    ④ 美术 + ⑤ 配音 + ⑥ 合成，SSE 推送进度。
    """
    ep = db.get_episode(eid)
    if not ep:
        raise HTTPException(404, "剧集不存在")
    project = db.get_project(ep["project_id"])
    if not project:
        raise HTTPException(404, "项目不存在")

    # 允许本次渲染临时覆盖 provider / 镜头模式
    if req and req.image_provider:
        config.IMAGE_PROVIDER = req.image_provider
    if req and req.tts_provider:
        config.TTS_PROVIDER = req.tts_provider
    video_mode = (req.video_mode if req and req.video_mode else config.VIDEO_MODE)
    if video_mode not in ("kenburns", "still", "t2v"):
        raise HTTPException(400, f"不支持的镜头模式: {video_mode}")

    # 🔒 付费总开关：关闭时强制回落全部免费实现，任何请求都无法产生费用
    if not config.ALLOW_PAID:
        config.IMAGE_PROVIDER = "placeholder"
        config.TTS_PROVIDER = "placeholder"
        if video_mode == "t2v":
            video_mode = "kenburns"
        logger.info("付费能力已关闭（STUDIO_ALLOW_PAID=0），本次使用全免费实现")

    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def cb(stage: str, pct: int, msg: str) -> None:
        loop.call_soon_threadsafe(
            queue.put_nowait,
            {"type": "progress", "stage": stage, "pct": pct, "msg": msg},
        )

    async def runner() -> None:
        try:
            res = await asyncio.to_thread(
                agents.render_episode, ep, project, cb, video_mode
            )
            providers_used = res.pop("_providers", [])
            loop.call_soon_threadsafe(
                queue.put_nowait,
                {"type": "done", "episode": res,
                 "providers": providers_used, "video_mode": video_mode},
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("渲染失败")
            loop.call_soon_threadsafe(
                queue.put_nowait, {"type": "error", "message": str(e)}
            )

    async def gen():
        task = asyncio.create_task(runner())
        yield sse({"type": "start", "episode_id": eid,
                   "shots": len(ep.get("shots") or [])})
        try:
            while True:
                item = await queue.get()
                yield sse(item)
                if item.get("type") in ("done", "error"):
                    break
        finally:
            if not task.done():
                task.cancel()
        yield sse({"type": "close"})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/episodes/{eid}/download")
async def download(eid: str):
    ep = db.get_episode(eid)
    if not ep or not ep.get("video"):
        raise HTTPException(404, "还没有生成视频")
    p = config.MEDIA_DIR / ep["video"]
    if not p.exists():
        raise HTTPException(404, "视频文件不存在")
    return FileResponse(p, filename=f"{ep.get('title', 'episode')}.mp4",
                        media_type="video/mp4")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="127.0.0.1", port=8100, reload=False)