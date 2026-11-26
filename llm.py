"""LLM 调用封装：纯文本 + 健壮的 JSON 解析

踩过的坑：deepseek-v4.1 是推理模型，推理过程也消耗 max_tokens。
长任务（如 10+ 镜头的分镜表）容易被截断，content 返回空字符串。
所以这里：① 默认给足 token ② 空内容自动翻倍重试 ③ 错误信息带上 finish_reason
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

import config

logger = logging.getLogger(__name__)

_client = None


def _get_client():
    global _client
    if _client is None:
        from openai import OpenAI

        if not config.API_KEY:
            raise RuntimeError("缺少 API Key：请设置环境变量 DASHSCOPE_API_KEY")
        _client = OpenAI(
            api_key=config.API_KEY,
            base_url=config.LLM_BASE_URL,
            timeout=config.LLM_TIMEOUT,
        )
    return _client


@dataclass
class LLMResult:
    content: str
    reasoning: str
    finish_reason: str
    completion_tokens: int


def call_raw(system: str, user: str, *, temperature: float | None = None,
             max_tokens: int = 6000) -> LLMResult:
    resp = _get_client().chat.completions.create(
        model=config.LLM_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=config.LLM_TEMPERATURE if temperature is None else temperature,
        max_tokens=max_tokens,
    )
    choice = resp.choices[0]
    msg = choice.message
    return LLMResult(
        content=(msg.content or "").strip(),
        reasoning=(getattr(msg, "reasoning_content", None) or ""),
        finish_reason=str(choice.finish_reason),
        completion_tokens=int(getattr(resp.usage, "completion_tokens", 0) or 0),
    )


def call_text(system: str, user: str, *, temperature: float | None = None,
              max_tokens: int = 6000) -> str:
    r = call_raw(system, user, temperature=temperature, max_tokens=max_tokens)
    if not r.content and r.finish_reason == "length":
        # 推理把预算吃光了，加倍重试
        logger.warning("内容为空且被截断（推理 %d 字），加倍 token 重试",
                       len(r.reasoning))
        r = call_raw(system, user, temperature=temperature,
                     max_tokens=min(max_tokens * 2, 16000))
    if not r.content:
        raise RuntimeError(
            f"模型返回空内容（finish_reason={r.finish_reason}，"
            f"推理 {len(r.reasoning)} 字，completion_tokens={r.completion_tokens}）"
        )
    return r.content


# ---------------------------------------------------------------- JSON 解析
_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)


def _strip_fence(text: str) -> str:
    m = _FENCE_RE.search(text)
    return m.group(1).strip() if m else text.strip()


def _extract_json_block(text: str) -> str:
    """用括号配对抠出第一个完整 JSON（台词里可能有花括号，不能用贪婪正则）"""
    text = _strip_fence(text)
    start = opener = None
    for i, ch in enumerate(text):
        if ch in "{[":
            start, opener = i, ch
            break
    if start is None:
        raise ValueError(f"输出中没有 JSON，前 200 字：{text[:200]!r}")

    closer = "}" if opener == "{" else "]"
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    raise ValueError(f"JSON 不完整（可能被截断），前 200 字：{text[:200]!r}")


def _repair(s: str) -> str:
    return re.sub(r",\s*([}\]])", r"\1", s)


def call_json(system: str, user: str, *, temperature: float | None = None,
              max_tokens: int = 6000) -> Any:
    """要求 JSON 输出；空内容/截断/格式错误都会自动重试"""
    max_tok = max_tokens
    last_err = ""

    for attempt in range(3):
        try:
            raw = call_text(system, user, temperature=temperature, max_tokens=max_tok)
        except RuntimeError as e:
            # 空内容：加大 token 再来
            last_err = str(e)
            max_tok = min(max_tok * 2, 16000)
            logger.warning("第 %d 次失败（%s），token 提到 %d", attempt + 1, e, max_tok)
            continue

        try:
            return json.loads(_extract_json_block(raw))
        except (ValueError, json.JSONDecodeError) as e:
            last_err = str(e)
            try:
                return json.loads(_repair(_extract_json_block(raw)))
            except Exception:  # noqa: BLE001
                pass
            logger.warning("第 %d 次 JSON 解析失败：%s", attempt + 1, str(e)[:150])
            # 把错误回喂，要求它只输出 JSON
            user = (
                user
                + f"\n\n【重要】上一次输出不是合法 JSON（{str(e)[:120]}）。"
                  "请只输出一个合法 JSON 对象，不要任何解释文字、不要代码块标记。"
            )
            temperature = 0.2

    raise ValueError(f"连续 3 次未能得到合法 JSON：{last_err}")