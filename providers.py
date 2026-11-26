"""可插拔的生成能力：图像 / 语音 / 视频合成

设计要点：所有生成能力都抽象成 provider，
默认走 placeholder（本地、零成本），充值后改环境变量即可切真实模型。

    STUDIO_IMAGE_PROVIDER=dashscope
    STUDIO_TTS_PROVIDER=dashscope
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import struct
import subprocess
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path
from typing import Optional

import config

logger = logging.getLogger(__name__)


# ============================================================
# 图像
# ============================================================
_PALETTES = [
    ((18, 24, 38), (52, 74, 110)),    # 冷蓝夜
    ((42, 18, 30), (120, 48, 70)),    # 暗红
    ((20, 34, 28), (48, 96, 78)),     # 墨绿
    ((38, 30, 18), (120, 92, 40)),    # 暖褐
    ((26, 20, 40), (78, 60, 120)),    # 紫
    ((14, 30, 36), (38, 96, 108)),    # 青
]


def _pick_palette(key: str):
    h = hashlib.md5(key.encode("utf-8")).hexdigest()
    return _PALETTES[int(h[:4], 16) % len(_PALETTES)]


def _font(size: int, bold: bool = False):
    from PIL import ImageFont

    for p in config.FONT_CANDIDATES:
        if not os.path.exists(p):
            continue
        try:
            # .ttc 支持 index，1 一般是粗体
            idx = 1 if bold and p.lower().endswith(".ttc") else 0
            return ImageFont.truetype(p, size, index=idx)
        except Exception:  # noqa: BLE001
            try:
                return ImageFont.truetype(p, size)
            except Exception:  # noqa: BLE001
                continue
    return ImageFont.load_default()


def _wrap(text: str, font, max_width: int) -> list[str]:
    """按像素宽度折行（中文没有空格，按字符累加）"""
    lines: list[str] = []
    cur = ""
    for ch in text:
        if ch == "\n":
            lines.append(cur)
            cur = ""
            continue
        if font.getlength(cur + ch) > max_width and cur:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return lines


def _placeholder_image(shot: dict, out_path: Path) -> None:
    from PIL import Image, ImageDraw

    W, H = config.VIDEO_WIDTH, config.VIDEO_HEIGHT
    c1, c2 = _pick_palette(shot.get("desc") or shot.get("image_prompt") or "shot")
    img = Image.new("RGB", (W, H), c1)
    d = ImageDraw.Draw(img)

    # 垂直渐变
    for y in range(H):
        t = y / max(1, H - 1)
        # 轻微非线性，中间稍亮，做出"打光"感
        t2 = t ** 0.85
        col = (
            int(c1[0] + (c2[0] - c1[0]) * t2),
            int(c1[1] + (c2[1] - c1[1]) * t2),
            int(c1[2] + (c2[2] - c1[2]) * t2),
        )
        d.line([(0, y), (W, y)], fill=col)

    # 斜向光带
    for i in range(-H, W, 90):
        d.line([(i, H), (i + H, 0)], fill=(255, 255, 255, 6), width=1)

    # 暗角
    vig = Image.new("L", (W, H), 0)
    vd = ImageDraw.Draw(vig)
    for i in range(60):
        alpha = int(2 + i * 1.2)
        vd.rectangle([i * 3, i * 2, W - i * 3, H - i * 2], outline=alpha)
    img = Image.composite(Image.new("RGB", (W, H), (0, 0, 0)), img, vig)
    d = ImageDraw.Draw(img)

    f_slate = _font(20)
    f_desc = _font(30)
    f_sub = _font(31, bold=True)
    f_tiny = _font(15)

    # 顶部场记条
    d.rectangle([0, 0, W, 52], fill=(0, 0, 0))
    slate = (
        f"SHOT {shot.get('no', '?')}   "
        f"{shot.get('shot_type', '')} · {shot.get('camera', '')} · "
        f"{shot.get('duration', 0)}s"
    )
    d.text((24, 15), slate, font=f_slate, fill=(230, 236, 245))
    badge = "占位画面 PLACEHOLDER"
    bw = f_tiny.getlength(badge)
    d.text((W - bw - 24, 17), badge, font=f_tiny, fill=(150, 160, 175))

    # 中间画面描述
    desc = str(shot.get("desc") or "")
    lines = _wrap(desc, f_desc, W - 160)[:5]
    total_h = len(lines) * 44
    y0 = (H - total_h) // 2 - 20
    for i, ln in enumerate(lines):
        w = f_desc.getlength(ln)
        d.text(((W - w) / 2, y0 + i * 44), ln, font=f_desc, fill=(236, 240, 246))

    # 底部字幕
    dialogue = str(shot.get("dialogue") or "").strip()
    if dialogue:
        speaker = str(shot.get("speaker") or "").strip()
        # 模型有时把多个说话人塞进 speaker（如"陈小雨／陈默"），
        # 而台词里已经带了"某某：…"。这种情况再加前缀会重复，先判断一下。
        already_has_prefix = "：" in dialogue
        show_prefix = (
            speaker
            and speaker != "旁白"
            and "／" not in speaker and "/" not in speaker
            and not already_has_prefix
        )
        text = f"{speaker}：{dialogue}" if show_prefix else dialogue
        sub_lines = _wrap(text, f_sub, W - 140)[:2]
        bar_h = len(sub_lines) * 42 + 24
        d.rectangle([0, H - bar_h, W, H], fill=(0, 0, 0))
        for i, ln in enumerate(sub_lines):
            w = f_sub.getlength(ln)
            d.text(((W - w) / 2, H - bar_h + 12 + i * 42), ln, font=f_sub, fill=(255, 255, 255))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG")


def _dashscope_image(prompt: str, out_path: Path) -> Optional[Path]:
    """
    百炼文生图（原生异步任务接口）。
    当前账号额度耗尽会抛 403，调用方会降级到 placeholder。
    """
    key = config.API_KEY
    base = "https://dashscope.aliyuncs.com/api/v1"

    body = json.dumps({
        "model": config.IMAGE_MODEL,
        "input": {"prompt": prompt},
        "parameters": {"size": f"{config.VIDEO_WIDTH}*{config.VIDEO_HEIGHT}", "n": 1},
    }).encode()
    req = urllib.request.Request(
        base + "/services/aigc/text2image/image-synthesis",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + key,
            "X-DashScope-Async": "enable",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        d = json.loads(r.read())
    task_id = d.get("output", {}).get("task_id")
    if not task_id:
        raise RuntimeError(f"文生图任务提交失败: {d}")

    for _ in range(60):
        time.sleep(3)
        q = urllib.request.Request(
            f"{base}/tasks/{task_id}",
            headers={"Authorization": "Bearer " + key},
        )
        with urllib.request.urlopen(q, timeout=60) as r:
            s = json.loads(r.read())
        st = s.get("output", {}).get("task_status")
        if st == "SUCCEEDED":
            url = s["output"]["results"][0].get("url")
            out_path.parent.mkdir(parents=True, exist_ok=True)
            urllib.request.urlretrieve(url, out_path)
            return out_path
        if st in ("FAILED", "CANCELED"):
            raise RuntimeError(f"文生图失败: {s.get('output')}")
    raise RuntimeError("文生图超时")


def generate_image(shot: dict, out_path: Path) -> tuple[Path, str]:
    """
    返回 (图片路径, 实际使用的 provider)。
    真实生成失败时自动降级为占位图，保证流水线不中断。
    """
    if config.IMAGE_PROVIDER == "dashscope":
        try:
            p = _dashscope_image(shot.get("image_prompt") or shot.get("desc", ""), out_path)
            if p:
                return p, "dashscope:" + config.IMAGE_MODEL
        except (urllib.error.HTTPError, Exception) as e:  # noqa: BLE001
            logger.warning("真实文生图失败，降级为占位图: %s", e)
    _placeholder_image(shot, out_path)
    return out_path, "placeholder"


# ============================================================
# 语音
# ============================================================
def _write_silence(out_path: Path, seconds: float, rate: int = 44100) -> None:
    n = max(1, int(rate * max(0.2, seconds)))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * n)


def _write_tone(out_path: Path, seconds: float, freq: float = 220.0, rate: int = 44100) -> None:
    """占位音轨：极轻的环境音，避免成片完全静音显得像坏了"""
    n = max(1, int(rate * max(0.2, seconds)))
    amp = 900  # 很小声
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = bytearray()
        for i in range(n):
            # 双频叠加 + 淡入淡出，听起来像远处嗡鸣
            v = (math.sin(2 * math.pi * freq * i / rate) * 0.6
                 + math.sin(2 * math.pi * (freq * 1.5) * i / rate) * 0.4)
            env = min(1.0, i / (rate * 0.5), (n - i) / (rate * 0.5))
            s = int(v * amp * env)
            frames += struct.pack("<h", max(-32768, min(32767, s)))
        w.writeframes(bytes(frames))


def _dashscope_tts(text: str, out_path: Path) -> Optional[Path]:
    """百炼 TTS（原生接口），额度耗尽会 403"""
    key = config.API_KEY
    body = json.dumps({
        "model": config.TTS_MODEL,
        "input": {"text": text, "voice": config.TTS_VOICE},
    }).encode()
    req = urllib.request.Request(
        "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        d = json.loads(r.read())
    audio = d.get("output", {}).get("audio", {})
    url = audio.get("url") if isinstance(audio, dict) else None
    if not url:
        raise RuntimeError(f"TTS 未返回音频: {str(d)[:200]}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(url, out_path)
    return out_path


def synthesize_speech(text: str, out_path: Path, seconds: float) -> tuple[Path, str]:
    if config.TTS_PROVIDER == "dashscope" and text.strip():
        try:
            p = _dashscope_tts(text, out_path)
            if p:
                return p, "dashscope:" + config.TTS_MODEL
        except Exception as e:  # noqa: BLE001
            logger.warning("真实 TTS 失败，降级为占位音轨: %s", e)
    # 占位：按估算时长生成极轻环境音
    _write_tone(out_path, seconds)
    return out_path, "placeholder"


# ============================================================
# 视频合成
# ============================================================
def ffmpeg_exe() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def _run(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg 失败({r.returncode}): {r.stderr[-800:]}")


def _kenburns_clip(img: Path, dur: float, out_path: Path, zoom_in: bool) -> Path:
    """
    静帧 → 缓慢推近/拉远的动态片段（Ken Burns 效果）。
    先把画面放大 2 倍再 zoompan，避免缩放抖动和边缘露黑。
    """
    ff = ffmpeg_exe()
    W, H, FPS = config.VIDEO_WIDTH, config.VIDEO_HEIGHT, config.VIDEO_FPS
    frames = max(2, int(dur * FPS))
    step = config.ZOOM_RANGE / max(1, frames)
    if zoom_in:
        zexpr = f"min(1+{step:.6f}*on,{1 + config.ZOOM_RANGE:.3f})"
    else:
        zexpr = f"max({1 + config.ZOOM_RANGE:.3f}-{step:.6f}*on,1)"

    vf = (
        f"scale={W * 2}:{H * 2}:force_original_aspect_ratio=increase,"
        f"crop={W * 2}:{H * 2},"
        f"zoompan=z='{zexpr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        f":d=1:s={W}x{H}:fps={FPS},"
        f"format=yuv420p"
    )
    _run([
        ff, "-y", "-loop", "1", "-i", str(img),
        "-t", f"{dur:.2f}", "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", str(out_path),
    ])
    return out_path


def _dashscope_t2v(prompt: str, out_path: Path, duration: int) -> Path:
    """
    文生视频（原生异步任务）。需要付费额度。
    产出的是真实动态画面，成本约 0.2~2 元/秒，注意控制镜头数量。
    """
    key = config.API_KEY
    base = "https://dashscope.aliyuncs.com/api/v1"

    body = json.dumps({
        "model": config.T2V_MODEL,
        "input": {"prompt": prompt},
        "parameters": {
            "size": f"{config.VIDEO_WIDTH}*{config.VIDEO_HEIGHT}",
            "duration": duration,
        },
    }).encode()
    req = urllib.request.Request(
        base + "/services/aigc/video-generation/video-synthesis",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + key,
            "X-DashScope-Async": "enable",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        d = json.loads(r.read())
    task_id = d.get("output", {}).get("task_id")
    if not task_id:
        raise RuntimeError(f"文生视频提交失败: {str(d)[:300]}")

    for _ in range(180):          # 视频生成较慢，最多等 9 分钟
        time.sleep(3)
        q = urllib.request.Request(
            f"{base}/tasks/{task_id}",
            headers={"Authorization": "Bearer " + key},
        )
        with urllib.request.urlopen(q, timeout=60) as r:
            s = json.loads(r.read())
        st = s.get("output", {}).get("task_status")
        if st == "SUCCEEDED":
            out = s["output"]
            # 实测 wan2.7-t2v 的地址在 output.video_url，
            # 而部分模型放在 output.results[0].video_url —— 两种都兼容
            url = out.get("video_url") or ""
            if not url:
                results = out.get("results") or []
                if results:
                    url = results[0].get("video_url") or results[0].get("url") or ""
            if not url:
                raise RuntimeError(f"文生视频无返回地址: {str(out)[:300]}")
            out_path.parent.mkdir(parents=True, exist_ok=True)
            urllib.request.urlretrieve(url, out_path)
            return out_path
        if st in ("FAILED", "CANCELED"):
            raise RuntimeError(f"文生视频失败: {str(s.get('output'))[:300]}")
    raise RuntimeError("文生视频超时")


def make_shot_clips(shots: list[dict], work_dir: Path,
                    mode: str | None = None,
                    cb=None) -> list[Path]:
    """
    把每个镜头变成一段视频片段。
    mode:
      kenburns / still → 用分镜图做静帧推拉（免费）
      t2v              → 调文生视频模型（付费，失败自动回落 kenburns）
    返回片段路径列表（顺序与镜头一致）。
    """
    mode = mode or config.VIDEO_MODE
    work_dir.mkdir(parents=True, exist_ok=True)
    clips: list[Path] = []
    total = len(shots)

    for i, s in enumerate(shots):
        img = s.get("image_abs")
        dur = max(0.5, float(s.get("duration") or 2.5))
        clip = work_dir / f"clip_{i:03d}.mp4"

        if mode == "t2v" and config.T2V_MAX_SHOTS > 0:
            # 只对前 T2V_MAX_SHOTS 个镜头用付费文生视频（默认 0 = 完全关闭）
            if i < config.T2V_MAX_SHOTS:
                prompt = s.get("image_prompt") or s.get("desc") or ""
                if cb:
                    cb(i, total, f"文生视频 {i + 1}/{min(config.T2V_MAX_SHOTS, total)}")
                try:
                    _dashscope_t2v(prompt, clip, config.T2V_DURATION)
                    clips.append(clip)
                    continue
                except Exception as e:  # noqa: BLE001
                    logger.warning("镜头 %s 文生视频失败，回落推拉镜头: %s", s.get("no"), e)

        # 静帧推拉（默认路径）
        if not img or not Path(img).exists():
            logger.warning("镜头 %s 没有画面，跳过", s.get("no"))
            continue
        if mode in ("kenburns", "t2v"):
            _kenburns_clip(Path(img), dur, clip, zoom_in=(i % 2 == 0))
        else:
            # 纯静态
            vf = (
                f"scale={config.VIDEO_WIDTH}:{config.VIDEO_HEIGHT}:"
                f"force_original_aspect_ratio=decrease,"
                f"pad={config.VIDEO_WIDTH}:{config.VIDEO_HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=black,"
                f"format=yuv420p"
            )
            _run([
                ffmpeg_exe(), "-y", "-loop", "1", "-i", str(img),
                "-t", f"{dur:.2f}", "-r", str(config.VIDEO_FPS), "-vf", vf,
                "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                str(clip),
            ])
        clips.append(clip)

    return clips


def concat_clips(clips: list[Path], audio_path, out_path: Path,
                 work_dir: Path) -> Path:
    """把片段串起来并混入音轨"""
    if not clips:
        raise RuntimeError("没有任何可用片段，无法合成视频")
    ff = ffmpeg_exe()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    concat_txt = work_dir / "concat.txt"
    concat_txt.write_text(
        "\n".join(f"file '{c.as_posix()}'" for c in clips), encoding="utf-8"
    )

    if audio_path and Path(audio_path).exists():
        _run([
            ff, "-y",
            "-f", "concat", "-safe", "0", "-i", str(concat_txt),
            "-i", str(audio_path),
            "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
            "-shortest", "-movflags", "+faststart",
            str(out_path),
        ])
    else:
        _run([
            ff, "-y",
            "-f", "concat", "-safe", "0", "-i", str(concat_txt),
            "-c:v", "copy", "-movflags", "+faststart",
            str(out_path),
        ])
    return out_path


def build_video(shots: list[dict], audio_path, out_path: Path,
                work_dir: Path, mode: str | None = None,
                cb=None) -> Path:
    """兼容入口：分镜 → 片段 → 成片"""
    clips = make_shot_clips(shots, work_dir / "clips", mode, cb)
    return concat_clips(clips, audio_path, out_path, work_dir / "clips")