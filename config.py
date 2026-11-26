"""全局配置"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
MEDIA_DIR = DATA_DIR / "media"
DB_PATH = DATA_DIR / "studio.db"

DATA_DIR.mkdir(parents=True, exist_ok=True)
MEDIA_DIR.mkdir(parents=True, exist_ok=True)

# ============================================================
# 🔒 总开关：是否允许产生费用的调用
# ============================================================
# 默认 False —— 只走本地免费实现（占位画面/占位音/推拉镜头），绝不产生任何费用。
# 想开启付费能力，设环境变量 STUDIO_ALLOW_PAID=1，并同时配置下面各 provider。
ALLOW_PAID = os.getenv("STUDIO_ALLOW_PAID", "0") == "1"

# ---------------- 大模型（文本 Agent） ----------------
LLM_MODEL = os.getenv("STUDIO_LLM", "deepseek-v4.1-flash-1017")
LLM_BASE_URL = os.getenv(
    "STUDIO_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
)
API_KEY = os.getenv("DASHSCOPE_API_KEY") or os.getenv("OPENAI_API_KEY") or ""
LLM_TEMPERATURE = 0.8
LLM_TIMEOUT = 180

# ---------------- 图像生成 ----------------
# placeholder : 本地生成占位分镜卡（零成本，默认）
# dashscope   : 调用百炼文生图（需要额度）
IMAGE_PROVIDER = os.getenv("STUDIO_IMAGE_PROVIDER", "placeholder")
IMAGE_MODEL = os.getenv("STUDIO_IMAGE_MODEL", "qwen-image-2.0")

# ---------------- 语音合成 ----------------
# placeholder : 生成等长静音轨（零成本，默认）
# dashscope   : 调用百炼 TTS（需要额度）
TTS_PROVIDER = os.getenv("STUDIO_TTS_PROVIDER", "placeholder")
TTS_MODEL = os.getenv("STUDIO_TTS_MODEL", "qwen3-tts-flash")
TTS_VOICE = os.getenv("STUDIO_TTS_VOICE", "Cherry")

# ---------------- 视频合成 ----------------
VIDEO_WIDTH = 1280
VIDEO_HEIGHT = 720
VIDEO_FPS = 25
SEC_PER_CHAR = 0.35          # 中文台词估时：每字约 0.35 秒
MIN_SHOT_SEC = 2.0
MAX_SHOT_SEC = 8.0

# 镜头动态方式：
#   kenburns  —— 静帧缓慢推近/拉远（免费，默认）
#   still     —— 纯静态硬切（免费）
#   t2v       —— 文生视频（按秒计费，很贵）
VIDEO_MODE = os.getenv("STUDIO_VIDEO_MODE", "kenburns")
T2V_MODEL = os.getenv("STUDIO_T2V_MODEL", "wan2.7-t2v")
T2V_DURATION = int(os.getenv("STUDIO_T2V_DURATION", "5"))

# ⚠️ 付费开关：默认 0 = 完全关闭文生视频（不产生任何费用）
# 想开启就设成具体数字，表示"只对前 N 个镜头用付费视频"
# 例如 1 约 5 元/集；3 约 15 元/集；14 约 70 元/集
T2V_MAX_SHOTS = int(os.getenv("STUDIO_T2V_MAX_SHOTS", "0"))

ZOOM_RANGE = 0.18            # kenburns 推拉幅度（18%）

# ---------------- 画面占位卡 ----------------
# 中文渲染必须用系统中文字体，否则全是方块
FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhl.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
]

# ---------------- 提示词 ----------------
CONCEPT_SYSTEM = """你是资深短剧策划。用户给一个创意方向，你要输出一份可拍摄的短剧方案。

要求：
- 剧名要有网感、能吸引点击（不超过 14 字）
- 题材明确（都市情感 / 悬疑 / 逆袭 / 古装 …）
- 一句话梗概要有钩子和反转暗示
- 主角 2-3 人，每个角色要有姓名、身份、性格、以及和主线的关系
- 分集大纲 3 集的量，每集一句话说明核心冲突

严格输出 JSON，不要输出任何解释文字、不要加 markdown 代码块。格式：
{
  "title": "剧名",
  "genre": "题材",
  "logline": "一句话梗概",
  "synopsis": "200字以内的故事梗概",
  "characters": [
    {"name": "姓名", "role": "身份/定位", "trait": "性格特点", "relation": "与主线关系"}
  ],
  "outline": [
    {"ep": 1, "title": "本集标题", "core": "本集核心冲突"}
  ]
}"""

SCRIPT_SYSTEM = """你是短剧编剧。根据给定的项目设定和分集大纲，写出指定这一集的完整剧本。

要求：
- 时长按 1 分钟左右设计，场景 3-5 个
- 对白要口语化、有节奏、每句尽量短（便于配音和做字幕）
- 开头 3 秒必须抓住人（冲突 / 悬念 / 反差）
- 结尾留钩子
- 场景描述要具体到可拍摄（地点、时间、关键动作）

严格输出 JSON，不要解释文字、不要 markdown 代码块。格式：
{
  "title": "本集标题",
  "scenes": [
    {
      "location": "地点",
      "time": "夜/日/黄昏",
      "action": "场景画面与动作描述（60字内）",
      "dialogues": [
        {"speaker": "角色名或旁白", "line": "台词"}
      ]
    }
  ]
}"""

STORYBOARD_SYSTEM = """你是分镜师。把剧本拆成可直接拍摄的分镜表。

要求：
- 每个场景拆成 1-3 个镜头，全片总镜头数控制在 8-14 个
- 景别用：远景 / 全景 / 中景 / 近景 / 特写
- camera 用：固定 / 推镜 / 拉镜 / 摇镜 / 跟拍 / 俯拍 / 仰拍
- desc 是给画面生成模型的描述，要具体（人物、动作、环境、光线、氛围），60字内
- image_prompt 是英文画面提示词，用于文生图，包含 cinematic / film still / 光线 / 镜头等关键词
- dialogue 是这个镜头承载的台词，没有台词就填空字符串
- duration 按台词长度估算（每秒约 3 个字），无台词给 2.5

严格输出 JSON，不要解释文字、不要 markdown 代码块。格式：
{
  "shots": [
    {
      "no": 1,
      "shot_type": "特写",
      "camera": "推镜",
      "desc": "中文画面描述",
      "image_prompt": "english prompt for image generation",
      "speaker": "说话人",
      "dialogue": "台词",
      "duration": 4.0
    }
  ]
}"""