# AI 数字人对话系统

> 一个从零实现的精简 AI 数字人：**听 → 想 → 说 → 动** 全链路可运行。编排层与前端为自研实现，底层调用 DeepSeek（LLM）、Edge-TTS（语音合成）与 Faster-Whisper（语音识别），可插拔架构，不依赖高端 GPU。

前端展示一个 2D 数字人形象，支持**文字或语音**输入：后端调用大模型**流式**生成回复，按句合成语音并逐句返回，网页端边接收边播放，用音频实时驱动数字人的「嘴巴」做口型动画。

---

## ✨ 亮点

1. **可插拔 Provider 架构**：LLM / TTS / STT 各自抽象成接口，DeepSeek / Edge-TTS / Faster-Whisper 可自由切换，改 `.env` 即可，上层编排零改动。
2. **语音对话**：麦克风录音 → 本地 Faster-Whisper 识别 → 数字人语音回复，真正的「语音数字人」。
3. **语音输入面板**：对话框下方实时显示**音量电平条**（带 VAD 阈值刻度）与**判定时间条**——停止说话后倒计时开始缩短，走完自动识别并发送；停顿时长可在 1–4 秒之间调节。
4. **打断（barge-in）**：数字人说话时持续监听人声，你开口并持续约 0.3 秒，就会立即停止播报、中断生成并转入聆听；用更高的阈值 + 回声抑制避免被自己的声音打断。
5. **流式低延迟**：LLM 边生成、边分句、边 TTS，逐句返回，首句即播，显著降低首字延迟。
6. **无 GPU 依赖**：LLM、TTS 走 API，口型用 Web Audio 实时驱动，普通电脑即可运行。
7. **多轮对话**：前端维护对话历史，后端统一注入数字人角色设定（system prompt）。
8. **开箱即用**：便携包内置 Python 运行时与 Faster-Whisper base 模型，解压后双击
   `start.bat` 即可；页面首次打开自带分步「使用说明」，右上角可随时再看。

## 🏗️ 架构

```
浏览器前端 (SVG 数字人 + 口型动画 + 语音输入面板)
        │  HTTP /api/chat/stream · /api/stt · /api/voice/config
        ▼
FastAPI 后端 (main.py)
        │
        ▼
编排层 (pipeline.py)  ── 注入角色设定 → 流式分句 → 串联各 provider
        │
        ├── STT Provider  (Faster-Whisper)
        ├── LLM Provider  (Mock / DeepSeek)
        └── TTS Provider  (Edge-TTS)
```

## 🚀 快速开始

环境要求：Python 3.10+（本项目在 3.11 测试）。

```bash
# 1. 进入项目
cd digital-human

# 2. 创建虚拟环境并安装依赖
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux / macOS:
source .venv/bin/activate

pip install -r requirements.txt

# 3. 配置（可选，默认用 Mock LLM 即可跑通）
cp .env.example .env

# 4. 启动
uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

浏览器打开 <http://localhost:8000>，输入文字即可看到数字人张嘴回复。

> **想接入真实大模型**：编辑 `.env`，把 `LLM_PROVIDER=deepseek` 并填入 `DEEPSEEK_API_KEY`，重启即可。
>
> **首次语音识别**会通过 hf-mirror 下载 Faster-Whisper `base` 模型（约 150MB），请耐心等待；
> 之后复用本地缓存，不再联网。便携测试包已内置 `models/faster-whisper-base/`，
> 开箱即可离线识别；该目录不提交到 Git 仓库（见 `.gitignore`）。

### 安全说明

本项目是本地演示，**没有鉴权**。`--host 0.0.0.0` 会把对话接口和你的 API Key 额度暴露给局域网，
默认请用 `--host 127.0.0.1`；需要公网部署时请在前面加鉴权与限流。

### 运行测试

```bash
pip install pytest httpx
pytest -q
```

测试用 fake pipeline 覆盖流式协议：正常逐句事件、生成中途异常必须变成 `error` 事件、
请求体角色/长度校验、STT 非法音频返回 4xx / 上游失败返回 502、中文识别参数确实传给
faster-whisper，以及语音面板参数接口的默认值与可调范围。

## 📁 目录结构

```
digital-human/
├── backend/
│   ├── main.py             # FastAPI 入口 + 对话 API + 托管前端
│   ├── pipeline.py         # 编排层：串联 LLM → TTS
│   ├── config.py           # 配置管理
│   └── providers/          # 可插拔 Provider 层
│       ├── llm.py          #   MockLLMProvider / DeepSeekProvider
│       ├── tts.py          #   EdgeTTSProvider
│       └── stt.py          #   FasterWhisperSTTProvider
├── frontend/
│   ├── index.html          # 页面结构：数字人 + 对话框 + 语音面板 + 使用说明弹窗
│   ├── styles.css          # 样式
│   ├── voice-panel.js      # 语音面板：麦克风 / 音量条 / 判定时间条 / 打断监听
│   └── app.js              # 对话、流式回复、音频播放、口型与使用说明
├── models/                 # 内置识别模型（仅便携包，已 gitignore）
├── tests/                  # 流式协议、STT 与语音面板参数回归测试
├── requirements.txt
└── .env.example
```

## 🗺️ Roadmap

- [x] 文字对话 + TTS 语音 + 口型动画
- [x] 语音输入（STT）：麦克风说话 → 数字人回应
- [x] 语音输入面板：音量条 + 判定时间条（可调停顿 1–4 秒）
- [x] 打断（barge-in）：数字人说话时检测到人声立即停止播报
- [x] 流式输出：LLM 流式生成 + 分句 TTS，降低首字延迟
- [ ] 声音克隆 / 更多音色
- [ ] 3D 形象（Live2D / 3D 模型）替代 2D SVG
- [ ] RAG 知识库：让数字人回答特定领域问题
- [ ] 打断功能：用户插话时中断当前回复

## 📄 License

MIT
