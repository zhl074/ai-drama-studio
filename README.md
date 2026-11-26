# AI 多模态创作与短剧生成 Agent 平台

输入一句创意 → 6 个 Agent 自动协作 → 输出一部带画面、字幕、音轨的短剧 MP4。

**技术栈**：FastAPI · 通义千问 · Pillow · ffmpeg · SQLite · 原生 JS

> 🔒 **默认零成本运行**：所有生成能力默认走本地免费实现，
> 画面是分镜占位卡、配音是占位音轨、镜头动态用本地 ffmpeg 推拉。
> 完整开发调试不会产生任何 API 费用。

---

## 一、它做什么

```
「外卖员暴雨夜送最后一单，收货人是三年前失踪的妹妹」
                        ↓
              Agent 流水线（6 步）
                        ↓
  剧本 + 14 个分镜 + 14 张画面 + 音轨 + 85 秒 MP4
```

### Agent 流水线

| 步骤 | Agent | 输入 | 输出 |
|---|---|---|---|
| ① | **策划** | 一句创意 | 剧名 / 题材 / 梗概 / 角色 / 分集大纲 |
| ② | **编剧** | 项目设定 + 分集大纲 | 场景化剧本（地点/时间/画面/对白） |
| ③ | **分镜** | 剧本 | 分镜表（景别/机位/画面描述/台词/时长） |
| ④ | **美术** | 分镜 | 每个镜头的画面 |
| ⑤ | **配音** | 台词 | 音轨 |
| ⑥ | **合成** | 画面 + 音轨 + 字幕 | MP4 成片 |

每个步骤都是独立的可重跑环节，前面失败不影响已产出的内容。

---

## 二、核心设计

### 1. 🔒 零成本总开关（默认开启保护）

```python
# config.py
ALLOW_PAID = os.getenv("STUDIO_ALLOW_PAID", "0") == "1"   # 默认 False
T2V_MAX_SHOTS = int(os.getenv("STUDIO_T2V_MAX_SHOTS", "0"))  # 默认 0
```

只要 `ALLOW_PAID=False`，**任何接口请求都无法产生费用**：

- `app.py` 在渲染入口强制把 `IMAGE_PROVIDER` / `TTS_PROVIDER` 重置为 `placeholder`
- 请求里传 `video_mode=t2v` 会被**静默降级**为 `kenburns`
- 前端 UI 里根本不显示付费选项

**实测安全验证**：

```
请求：{"video_mode":"t2v"}       ← 故意请求付费模式
返回：video_mode=kenburns        ← 被强制降级
      providers=placeholder      ← 全部本地实现
      [100%] 渲染完成            ← 成片正常输出
      💰 费用：0 元
```

即使以后误触，也不会扣钱。

### 2. 🎬 Ken Burns 推拉镜头（免费提升观感）

静帧占位图如果直接硬切，成片会像 PPT。这里用 ffmpeg 的 `zoompan` 让每张静帧**缓慢推近或拉远**，相邻镜头方向交替。

```python
# providers.py
frames = int(dur * FPS)
step = ZOOM_RANGE / frames           # 每帧增量
zexpr = f"min(1+{step}*on,{1+ZOOM_RANGE})"   # 推近
# 或 max(...-step*on, 1)                     拉远
```

关键细节：**先把画面放大 2 倍再 zoompan**，否则缩放会抖动、边缘露黑。

**实测验证**（同镜头内两个时间点抽帧，算像素差异）：

| 对比 | 差异像素占比 | 结论 |
|---|---|---|
| 同一镜头内 0.3s vs 5.2s | **29.7%** | ✅ 画面确实在运动 |
| 不同镜头之间 | 97.0% | 对照组，符合预期 |

### 3. 🔌 可插拔生成 Provider + 自动降级

所有生成能力抽象为 provider，真实调用失败时**自动回落**本地实现，流水线永不中断：

```python
IMAGE_PROVIDER = "placeholder"   # 或 "dashscope"
TTS_PROVIDER   = "placeholder"   # 或 "dashscope"
VIDEO_MODE     = "kenburns"      # 或 "still" / "t2v"
```

---

## 三、快速开始

### 1. 依赖

```powershell
cd C:\Users\lou\PycharmProjects\PythonProject\MultimodalDramaStudio
..\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 2. 设置 API Key（只用于文本 Agent，走免费额度）

```powershell
setx DASHSCOPE_API_KEY "sk-你的key"
```

### 3. 启动

```powershell
..\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8100
```

浏览器打开 **http://127.0.0.1:8100**

### 4. 使用

1. 点「＋ 新建短剧项目」，输入一句创意（有 5 个示例可点）
2. 策划完成 → 点「＋ 生成第 1 集剧本」
3. 选镜头模式（推拉 / 静态）→ 点「渲染成片」
4. 实时进度条 → 视频播放器出现 → 可下载 MP4

### 5. 费用自查

```powershell
..\.venv\Scripts\python.exe check_quota.py          # 免费探测，不产生费用
..\.venv\Scripts\python.exe check_quota.py --paid   # 真实提交，约 0.2 元
```

---

## 四、实测数据

用示例创意跑了一次完整流程（零成本模式）：

| 阶段 | 耗时 | 产出 |
|---|---|---|
| ① 策划 | 10.0 s | 《最后一单：收货人是妹妹》/ 都市悬疑情感 / 3 个角色 |
| ②③ 剧本+分镜 | 140.0 s | 《暴雨夜的最后一单》/ 14 个镜头 / 预估 85.75 s |
| ④⑤⑥ 渲染 | 12.7 s | 14 张画面 + 音轨 + 成片（推拉镜头） |
| **合计** | **约 163 s** | **MP4 17.9 MB / 85.75 秒 / 1280×720 / 25fps** |

成片校验（ffmpeg）：

```
Duration: 00:01:25.75
Video: h264 (High) 1280x720 25fps
Audio: aac (LC) 44100Hz mono
```

生成的分镜示例：

```
#4 [中景/推镜] 8.0s
   门缝里陈小雨眼神发空、往后缩半步，雨水在她脸侧反光；
   陈默湿透的脸僵在门外，下颌不停滴水。
   台词：陈小雨：你……认错人了吧。我不认识你。／陈默：我不会认错。你左眉上有颗痣。
```

---

## 五、成本参考（开启付费前必看）

| 环节 | 模型 | 单价 | 一集（14 镜头） |
|---|---|---|---|
| 文本 Agent（策划+剧本+分镜） | deepseek-v4.1-flash | 极低 | 几乎免费 |
| **文生图** | qwen-image-2.0 | 约 0.2 元/张 | 约 2.8 元 |
| **配音** | qwen3-tts-flash | 约 0.01 元/百字 | 约 0.1 元 |
| **文生视频** | wan2.7-t2v | **约 1 元/秒（1080P）** | **14×5 秒 ≈ 70 元** ⚠️ |
| 推拉镜头 | 本地 ffmpeg | **免费** | **0 元** |

**推荐组合**：
- 全免费：占位画面 + 占位音 + 推拉 → **0 元**
- 混合：前 1~3 个镜头用真实视频 + 其余推拉 → **5~15 元/集**
- 全真实：全部文生视频 → **约 70 元/集**（不建议）

开启付费需要同时设置：

```powershell
$env:STUDIO_ALLOW_PAID = "1"
$env:STUDIO_IMAGE_PROVIDER = "dashscope"     # 需先开通图像生成服务
$env:STUDIO_TTS_PROVIDER   = "dashscope"
$env:STUDIO_VIDEO_MODE     = "t2v"
$env:STUDIO_T2V_MAX_SHOTS  = "1"             # 限制付费镜头数量
```

---

## 六、目录结构

```
MultimodalDramaStudio/
├── app.py               # FastAPI 路由 + SSE 渲染进度 + 付费总开关拦截
├── agents.py            # 6 个 Agent 的流水线实现
├── llm.py               # LLM 调用 + 健壮 JSON 解析 + 空内容自动重试
├── providers.py         # 可插拔生成能力（占位图/Ken Burns/TTS/ffmpeg/文生视频）
├── db.py                # SQLite 持久层
├── config.py            # 提示词 + 全部可调参数 + 付费开关
├── check_quota.py       # 额度体检工具（默认不花钱）
├── static/index.html    # 工作台前端
├── data/
│   ├── studio.db        # 项目 / 剧集
│   └── media/           # 生成的画面、音轨、视频
├── requirements.txt
└── .gitignore
```

---

## 七、踩过的坑（真实记录）

### 1. 推理模型会把 JSON 输出"吃光"

`deepseek-v4.1-flash-1017` 是推理模型，**思考过程也消耗 max_tokens**。
生成 14 个镜头的分镜表时，实测推理用了 **15946 字**，直接耗尽预算，
`content` 返回空字符串 → JSON 解析失败。

日志证据：

```
WARNING 内容为空且被截断（推理 15946 字），加倍 token 重试
```

**三层防御**（`llm.py`）：

- 默认 6000 token，长任务给 8000
- 检测 `finish_reason=length` 且内容为空 → **token 翻倍重试**（上限 16000）
- JSON 解析失败 → 把错误信息回喂模型要求重输（最多 3 次）

### 2. 文生视频的返回结构不在 `results` 里

提交任务后 `task_status=SUCCEEDED`，但 `output.results` 是**空数组**。
真实地址在 **`output.video_url`**：

```json
{
  "output": {
    "task_status": "SUCCEEDED",
    "video_url": "https://dashscope-....oss-accelerate.aliyuncs.com/....mp4?Expires=..."
  },
  "usage": {"output_video_duration": 5, "SR": 1080, "ratio": "16:9"}
}
```

代码里两种结构都兼容，避免换模型后又踩一次。

### 3. 文生图接口走不通（`400 url error`）

所有图像模型（qwen-image-2.0 / wan2.7-image / z-image-turbo）在
`/services/aigc/text2image/image-synthesis` 都返回：

```
{"code":"InvalidParameter","message":"url error, please check url！"}
```

试过 6 个候选路径和兼容接口 `/images/generations`，全部失败。
文生视频用 `/services/aigc/video-generation/video-synthesis` 却成功——
说明**不是额度问题，是图像生成服务未开通或端点不匹配**。

### 4. PowerShell 的 UTF-8 陷阱

- 发送：`Invoke-RestMethod` 传中文 JSON 会按非 UTF-8 编码，中文变 `?`（不可逆）
  → 必须 `[System.Text.Encoding]::UTF8.GetBytes($body)` 再传
- 接收：PowerShell 会按 Latin-1 解码，中文显示成 `æåä¸å`
  → 接口本身没问题；服务端已显式声明 `application/json; charset=utf-8`

### 5. 中文渲染必须指定字体

Pillow 默认字体不含中文，画面上的中文全变方块。
必须用 `C:/Windows/Fonts/msyh.ttc`（微软雅黑）等系统中文字体。

### 6. 字幕前缀重复

模型会把多个说话人塞进 `speaker` 字段（如「陈小雨／陈默」），
而台词本身已带「某某：…」，再加前缀就变成「陈小雨／陈默：陈小雨：…」。
**解决**：speaker 含 `/` 或台词已含 `：` 时不加前缀。

---

## 八、已知限制

1. **默认画面是占位分镜卡**，不是真实图像（文生图服务需单独开通）
2. **默认音轨是占位环境音**，不是真人配音
3. **没有转场特效**，镜头之间是硬切（推拉只在镜头内部运动）
4. **单线程渲染**：镜头串行生成，镜头多了会慢
5. **无用户系统**：单机单用户，多人用需加鉴权和数据隔离
6. **剧本质量随机**：同一创意每次结果不同，没有 best-of-n 挑选

---

## 九、可以继续加的东西

| 优先级 | 功能 | 说明 |
|---|---|---|
| ⭐⭐⭐ | **转场特效** | 淡入淡出 / 叠化，ffmpeg `xfade` 一条滤镜即可 |
| ⭐⭐⭐ | **开通文生图服务** | 一旦可用，单集成本降到约 3 元 |
| ⭐⭐ | **BGM 与音效** | 加背景音乐轨，氛围提升明显 |
| ⭐⭐ | **风格预设** | 悬疑/甜宠/古风 → 影响提示词和配色 |
| ⭐⭐ | **剧本可视化编辑** | 允许手改台词和画面描述再渲染 |
| ⭐ | **多集连续生成** | 一次 3 集，保持角色和伏笔连贯 |
| ⭐ | **成片自动打分** | 让视觉模型给成片打分，挑最优版本 |

---

## 十、项目定位

这是一个**架构完整、但生成能力可插拔**的短剧 Agent 平台：

- 流水线、状态管理、SSE 进度、持久化、视频合成 —— **工程部分是真实可用的**
- 生成质量取决于接哪个模型 —— **默认免费档，随时可升级**
- 核心价值在于**把「一句创意 → 一部片子」的完整链路跑通**，
  而不是堆砌模型调用

零成本就能完整演示整条链路，这是它区别于"调个 API 就算做完了"的地方。