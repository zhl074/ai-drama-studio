"""
百炼额度体检工具

⚠️ 默认模式不花钱：只测文本模型（走免费额度），不会发起任何付费生成任务。

用法：
    ..\.venv\Scripts\python.exe check_quota.py
        只测文本 + 显示各生成能力的参考单价，不产生费用

    ..\.venv\Scripts\python.exe check_quota.py --paid
        真实提交 1 张图片 + 1 段短语音，约 0.2~0.3 元

    ..\.venv\Scripts\python.exe check_quota.py --paid --video
        额外提交 1 段 5 秒视频，约 5 元（1080P、约 1 元/秒）

百炼控制台：https://bailian.console.aliyun.com/
费用明细：https://billing-cost.console.aliyun.com/
"""

import json
import os
import sys
import urllib.error
import urllib.request

KEY = os.getenv("DASHSCOPE_API_KEY") or os.getenv("OPENAI_API_KEY")
if not KEY:
    print("❌ 未设置环境变量 DASHSCOPE_API_KEY")
    sys.exit(1)

V1 = "https://dashscope.aliyuncs.com/api/v1"
COMPAT = "https://dashscope.aliyuncs.com/compatible-mode/v1"

# 参考单价（阿里云百炼公开定价，仅供估算，实际以账单为准）
PRICES = {
    "文本 deepseek-v4.1-flash": "约 0.001 元/千 token",
    "文生图 qwen-image-2.0": "约 0.2 元/张",
    "语音 qwen3-tts-flash": "约 0.01 元/百字",
    "文生视频 wan2.7-t2v": "约 1 元/秒（1080P）→ 5 秒约 5 元",
}

HINTS = {
    "AllocationQuota.FreeTierOnly":
        "账号处于「仅使用免费额度」模式 → 百炼控制台关闭该开关，"
        "并确认账户有现金余额（代金券不能抵欠费）",
    "Arrearage": "账户欠费 → 费用中心充值后重试",
    "Throttling.RateQuota": "触发限流 → 等几分钟重试",
    "InvalidParameter": "参数或接口路径不匹配（该模型可能未开通/未开放）",
    "AccessDenied": "该模型对本账号/工作空间未开放，需要在控制台开通",
}


def post(url: str, data: dict, extra: dict | None = None) -> tuple[bool, str, dict]:
    h = {"Content-Type": "application/json", "Authorization": "Bearer " + KEY}
    if extra:
        h.update(extra)
    req = urllib.request.Request(
        url, data=json.dumps(data).encode(), headers=h, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            body = json.loads(r.read())
        if "task_id" in json.dumps(body):
            return True, "提交成功（会产生计费）", body
        if body.get("choices"):
            return True, (body["choices"][0]["message"].get("content") or "")[:16], body
        return True, "OK", body
    except urllib.error.HTTPError as e:
        txt = e.read().decode("utf-8", "replace")
        try:
            code = json.loads(txt).get("code", "")
        except Exception:  # noqa: BLE001
            code = txt[:60]
        return False, f"{e.code} {code}".strip(), {}
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}", {}


def main() -> None:
    args = set(sys.argv[1:])
    paid = "--paid" in args
    video = "--video" in args

    print("=" * 68)
    print("百炼额度体检")
    print("=" * 68)
    print("模式:", "付费探测（会产生少量费用）" if paid else "免费探测（不产生费用）")
    print()

    # ---- 1. 免费：文本 ----
    ok, msg, _ = post(
        f"{COMPAT}/chat/completions",
        {"model": "deepseek-v4.1-flash-1017",
         "messages": [{"role": "user", "content": "回复OK"}], "max_tokens": 10},
    )
    print(f"  {'✅' if ok else '❌'}  文本（免费额度）   {msg}")

    if not paid:
        print()
        print("-" * 68)
        print("生成能力参考单价（未实际调用，避免扣费）：")
        for k, v in PRICES.items():
            print(f"    · {k:<28} {v}")
        print("-" * 68)
        print("要真实测试生成能力，加 --paid（约 0.2~0.3 元）")
        print("要连视频一起测，再加 --video（约 5 元）")
        return

    # ---- 2. 付费：图片 ----
    est = 0.2
    print(f"\n  正在提交文生图（预估 {est} 元）…")
    ok, msg, body = post(
        f"{V1}/services/aigc/text2image/image-synthesis",
        {"model": "qwen-image-2.0", "input": {"prompt": "测试画面，一张图"},
         "parameters": {"size": "1280*720", "n": 1}},
        {"X-DashScope-Async": "enable"},
    )
    print(f"  {'✅' if ok else '❌'}  文生图 qwen-image-2.0   {msg}")
    if ok:
        print(f"      task_id: {body.get('output', {}).get('task_id')}")

    # ---- 3. 付费：语音 ----
    est += 0.01
    print(f"\n  正在提交语音合成（预估 0.01 元）…")
    ok2, msg2, _ = post(
        f"{V1}/services/aigc/multimodal-generation/generation",
        {"model": "qwen3-tts-flash",
         "input": {"text": "测试", "voice": "Cherry"}},
    )
    print(f"  {'✅' if ok2 else '❌'}  语音 qwen3-tts-flash      {msg2}")

    # ---- 4. 付费：视频（要显式 --video）----
    if video:
        est += 5
        print(f"\n  ⚠️  正在提交文生视频（5 秒 1080P，预估 5 元）…")
        ok3, msg3, body3 = post(
            f"{V1}/services/aigc/video-generation/video-synthesis",
            {"model": "wan2.7-t2v", "input": {"prompt": "测试画面，五秒镜头"},
             "parameters": {"size": "1280*720", "duration": 5}},
            {"X-DashScope-Async": "enable"},
        )
        print(f"  {'✅' if ok3 else '❌'}  文生视频 wan2.7-t2v     {msg3}")
        if ok3:
            print(f"      task_id: {body3.get('output', {}).get('task_id')}")
            print("      ⏱  约 90 秒后出结果，费用已产生")

    print()
    print("-" * 68)
    print(f"本次探测预估费用：约 {est:.2f} 元")
    print("实际扣费请查：https://billing-cost.console.aliyun.com/")
    print("-" * 68)


if __name__ == "__main__":
    main()