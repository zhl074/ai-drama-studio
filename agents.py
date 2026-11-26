"""Agent 流水线：策划 → 编剧 → 分镜 → 美术 → 配音 → 合成"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Optional

import config
import db
import llm
import providers

logger = logging.getLogger(__name__)

ProgressCb = Optional[Callable[[str, int, str], None]]


def _emit(cb: ProgressCb, stage: str, pct: int, msg: str) -> None:
    if cb:
        cb(stage, pct, msg)


def _rel(p: Path) -> str:
    return p.relative_to(config.MEDIA_DIR).as_posix()


def _abs(rel_or_path: str) -> Path:
    p = Path(rel_or_path)
    return p if p.is_absolute() else (config.MEDIA_DIR / p)


# ============================================================
# ① 策划 Agent
# ============================================================
def run_concept(idea: str) -> dict:
    user = f"创意方向：{idea}\n\n请按格式输出短剧方案 JSON。"
    data = llm.call_json(config.CONCEPT_SYSTEM, user, temperature=0.9, max_tokens=2500)

    title = str(data.get("title") or "未命名短剧").strip()[:30]
    genre = str(data.get("genre") or "未知").strip()
    logline = str(data.get("logline") or "").strip()
    synopsis = str(data.get("synopsis") or "").strip()
    characters = data.get("characters") or []
    outline = data.get("outline") or []
    if not isinstance(characters, list):
        characters = []
    if not isinstance(outline, list):
        outline = []
    if not outline:
        outline = [{"ep": 1, "title": title, "core": logline}]

    pid = db.create_project(
        title=title, genre=genre, logline=logline, synopsis=synopsis,
        characters=characters, outline=outline, idea=idea,
    )

    # 封面用占位图（复用同一套视觉风格）
    cover_path = config.MEDIA_DIR / pid / "cover.png"
    try:
        providers.generate_image(
            {"no": 0, "shot_type": "海报", "camera": "固定", "duration": "",
             "desc": title, "dialogue": logline, "speaker": ""},
            cover_path,
        )
        db.set_project_cover(pid, _rel(cover_path))
    except Exception as e:  # noqa: BLE001
        logger.warning("封面生成失败: %s", e)

    return db.get_project(pid) or {}


# ============================================================
# ② 编剧 Agent + ③ 分镜 Agent
# ============================================================
def _characters_text(project: dict) -> str:
    parts = []
    for c in project.get("characters") or []:
        parts.append(
            f"- {c.get('name','')}｜{c.get('role','')}｜{c.get('trait','')}｜"
            f"{c.get('relation','')}"
        )
    return "\n".join(parts) or "（无）"


def _outline_text(project: dict) -> str:
    parts = []
    for o in project.get("outline") or []:
        parts.append(f"第{o.get('ep','?')}集《{o.get('title','')}》：{o.get('core','')}")
    return "\n".join(parts) or "（无）"


def run_script(project: dict, ep_index: int) -> dict:
    outline_item = None
    for o in project.get("outline") or []:
        if int(o.get("ep") or 0) == ep_index:
            outline_item = o
            break
    ep_core = (outline_item or {}).get("core") or project.get("logline") or ""

    user = (
        f"剧名：{project.get('title')}\n"
        f"题材：{project.get('genre')}\n"
        f"梗概：{project.get('synopsis')}\n\n"
        f"角色：\n{_characters_text(project)}\n\n"
        f"分集大纲：\n{_outline_text(project)}\n\n"
        f"现在写第 {ep_index} 集，本集核心冲突：{ep_core}\n"
        "请输出剧本 JSON。"
    )
    data = llm.call_json(config.SCRIPT_SYSTEM, user, temperature=0.85, max_tokens=8000)
    if not isinstance(data.get("scenes"), list) or not data["scenes"]:
        raise ValueError("剧本没有生成有效场景")
    return data


def run_storyboard(project: dict, script: dict) -> list[dict]:
    scenes_txt = []
    for i, sc in enumerate(script.get("scenes") or [], 1):
        lines = "\n".join(
            f"    {d.get('speaker','')}：{d.get('line','')}"
            for d in (sc.get("dialogues") or [])
        )
        scenes_txt.append(
            f"场景{i}｜{sc.get('location','')}｜{sc.get('time','')}\n"
            f"  画面：{sc.get('action','')}\n{lines}"
        )

    user = (
        f"剧名：{project.get('title')}｜题材：{project.get('genre')}\n"
        f"角色：\n{_characters_text(project)}\n\n"
        f"剧本场景：\n" + "\n\n".join(scenes_txt) +
        "\n\n请拆分为分镜表 JSON。"
    )
    data = llm.call_json(config.STORYBOARD_SYSTEM, user, temperature=0.7, max_tokens=8000)
    shots = data.get("shots")
    if not isinstance(shots, list) or not shots:
        raise ValueError("分镜没有生成有效镜头")
    return normalize_shots(shots)


def normalize_shots(shots: list[dict]) -> list[dict]:
    """统一字段、重算时长，保证后续渲染稳定"""
    out = []
    for i, s in enumerate(shots, 1):
        dialogue = str(s.get("dialogue") or "").strip()
        # 时长以台词长度为准，保证配音和画面对齐
        if dialogue:
            sec = len(dialogue) * config.SEC_PER_CHAR
            sec = max(config.MIN_SHOT_SEC, min(config.MAX_SHOT_SEC, sec))
        else:
            try:
                sec = float(s.get("duration") or 2.5)
            except (TypeError, ValueError):
                sec = 2.5
            sec = max(config.MIN_SHOT_SEC, min(config.MAX_SHOT_SEC, sec))

        out.append({
            "no": i,
            "shot_type": str(s.get("shot_type") or "中景"),
            "camera": str(s.get("camera") or "固定"),
            "desc": str(s.get("desc") or "").strip(),
            "image_prompt": str(s.get("image_prompt") or s.get("desc") or "").strip(),
            "speaker": str(s.get("speaker") or "").strip(),
            "dialogue": dialogue,
            "duration": round(sec, 2),
            "image": "",
        })
    return out


def create_episode(project: dict, ep_index: int) -> dict:
    script = run_script(project, ep_index)
    shots = run_storyboard(project, script)
    title = str(script.get("title") or f"第{ep_index}集").strip()[:40]
    eid = db.create_episode(
        project_id=project["id"], ep_index=ep_index,
        title=title, script=script, shots=shots,
    )
    return db.get_episode(eid) or {}


# ============================================================
# ④ 美术 + ⑤ 配音 + ⑥ 合成
# ============================================================
def render_episode(episode: dict, project: dict, cb: ProgressCb = None,
                   mode: str | None = None) -> dict:
    """
    完整渲染：生成所有分镜画面 → 生成音轨 → 合成 MP4 → 落库
    mode 可覆盖镜头动态方式（kenburns / still / t2v）
    """
    shots = list(episode.get("shots") or [])
    if not shots:
        raise ValueError("这一集还没有分镜，请先生成剧本和分镜")

    eid, pid = episode["id"], project["id"]
    media_dir = config.MEDIA_DIR / pid / eid
    media_dir.mkdir(parents=True, exist_ok=True)

    total = len(shots)
    used_providers: set[str] = set()

    # 镜头模式提前确定：t2v 模式下前 N 个镜头是固定长度的真实视频，
    # 必须先把时长对齐，否则配音轨和画面对不上
    video_mode = mode or config.VIDEO_MODE
    if video_mode == "t2v":
        if config.T2V_MAX_SHOTS <= 0:
            # 付费开关关闭 → 静默回落免费推拉模式，绝不产生费用
            video_mode = "kenburns"
            _emit(cb, "plan", 2, "文生视频已关闭，使用免费推拉镜头")
        else:
            for s in shots[:config.T2V_MAX_SHOTS]:
                s["duration"] = float(config.T2V_DURATION)
            _emit(cb, "plan", 2,
                  f"混合模式：前 {min(config.T2V_MAX_SHOTS, total)} 个镜头用文生视频，"
                  f"其余用推拉镜头")

    # ---- 画面 ----
    for i, s in enumerate(shots):
        pct = int(5 + (i / total) * 60)
        _emit(cb, "image", pct, f"生成画面 {i + 1}/{total}（镜头 {s['no']}）")
        img_path = media_dir / f"shot_{s['no']:03d}.png"
        try:
            _, used = providers.generate_image(s, img_path)
            used_providers.add(used)
            s["image"] = _rel(img_path)
        except Exception as e:  # noqa: BLE001
            logger.warning("镜头 %s 画面生成失败: %s", s.get("no"), e)
            s["image"] = ""

        # 每生成一帧就更新库，前端可实时看到缩略图
        db.update_episode(eid, shots=shots)

    # ---- 配音 ----
    _emit(cb, "audio", 68, "生成配音音轨…")
    total_sec = sum(float(s.get("duration") or 0) for s in shots)
    audio_path = media_dir / "audio.wav"
    try:
        _, used = providers.synthesize_speech(
            f"第{episode.get('ep_index', 1)}集", audio_path, total_sec
        )
        used_providers.add(used)
    except Exception as e:  # noqa: BLE001
        logger.warning("配音生成失败，将输出无声视频: %s", e)
        audio_path = None  # type: ignore[assignment]

    # ---- 合成 ----
    _emit(cb, "video", 74, f"合成视频（{video_mode}）…")
    for s in shots:
        if s.get("image"):
            s["image_abs"] = str(_abs(s["image"]))
    out_path = media_dir / "episode.mp4"

    def clip_cb(i: int, total: int, msg: str) -> None:
        pct = 74 + int((i / max(1, total)) * 22)
        _emit(cb, "video", pct, msg)

    try:
        providers.build_video(shots, audio_path, out_path, media_dir,
                              mode=video_mode, cb=clip_cb)
        for s in shots:
            s.pop("image_abs", None)
        db.update_episode(
            eid, shots=shots, video=_rel(out_path),
            duration=round(total_sec, 2), status="rendered",
        )
        _emit(cb, "done", 100, "渲染完成")
    except Exception as e:  # noqa: BLE001
        for s in shots:
            s.pop("image_abs", None)
        db.update_episode(eid, shots=shots, status="failed")
        raise RuntimeError(f"视频合成失败: {e}") from e

    result = db.get_episode(eid) or {}
    result["_providers"] = sorted(used_providers)
    return result