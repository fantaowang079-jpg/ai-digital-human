# AI 数字人对话系统

> 一个从零实现的精简 AI 数字人：**听 → 想 → 说 → 动** 全链路可运行。编排层与前端为自研实现，底层调用 DeepSeek（LLM）、Edge-TTS（语音合成）与 Faster-Whisper（语音识别），可插拔架构，不依赖高端 GPU。

前端展示一个 2D 数字人形象，支持**文字或语音**输入：后端调用大模型**流式**生成回复，按句合成语音并逐句返回，网页端边接收边播放，用音频实时驱动数字人的「嘴巴」做口型动画。

还内置**本地知识库（RAG-lite）**与**双模式人格**：网页上传自己的文档后，「专业模式 · 知语」会严格依据知识库回答并标注引用；「心理医生模式 · 心语」则只和你轻松聊天。

---

## ✨ 亮点

1. **本地知识库（RAG-lite）**：上传 txt / md / csv / pdf / docx，自动切片建索引，提问时检索相关片段注入提示词；纯 Python TF-IDF 检索，无需向量数据库和 GPU。
2. **双模式对话**：专业模式「知语」严格引用知识库并展示来源；心理医生模式「心语」专注共情闲聊，两条链路互不干扰。
3. **PDF 转文字**：网页上传 PDF，提取纯文本并支持复制、下载 txt；扫描版 PDF 会提示需要 OCR。
4. **可插拔 Provider 架构**：LLM / TTS / STT 各自抽象成接口，DeepSeek / Edge-TTS / Faster-Whisper 可自由切换，改 `.env` 即可，上层编排零改动。
5. **语音对话**：麦克风录音 → 本地 Faster-Whisper 识别 → 数字人语音回复，真正的「语音数字人」。
6. **流式低延迟**：LLM 边生成、边分句、边 TTS，逐句返回，首句即播，显著降低首字延迟。
7. **无 GPU 依赖**：LLM、TTS 走 API，口型与知识库检索在本地用轻量方案实现，普通电脑即可运行。
8. **多轮对话**：前端维护对话历史，后端按模式注入人格设定与知识库上下文。

## 🏗️ 架构

```
浏览器前端 (SVG 数字人 + 口型动画 + 麦克风录音)
        │  HTTP /api/chat/stream · /api/stt
        ▼
FastAPI 后端 (main.py)
        │
        ▼
编排层 (pipeline.py)  ── 检索知识库 → 注入角色设定/资料 → 流式分句 → 串联各 provider
        │
        ├── 知识库 (knowledge.py)  ← knowledge/ 文档，TF-IDF 检索
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
> 之后复用本地缓存，不再联网。

## 🎭 两种模式

页面顶部可在两种人格之间切换；切换时会清空当前对话上下文，避免两种模式串味。

| 模式 | 名字 | 行为 |
| --- | --- | --- |
| 🧠 专业模式 | **知语** | 只依据知识库回答，每次检索至少 6 段资料；关键结论用（参考：文件名）标注，回答末尾列出参考来源，前端还会在气泡下方展示引用文件。资料不足时明确回答「知识库中没有找到相关信息」，不使用模型自身知识编造。 |
| 💬 心理医生模式 | **心语** | 不检索知识库，专注共情和倾听，陪你闲聊；不做医学诊断、不替代专业心理咨询，遇到严重危机时会建议寻求专业帮助。 |

两种人格都可以在 `.env` 中覆盖：

| 变量 | 说明 |
| --- | --- |
| `PROFESSIONAL_PROMPT` | 专业模式「知语」的完整人格提示词，留空使用内置默认 |
| `PSYCHOLOGIST_PROMPT` | 心理医生模式「心语」的完整人格提示词，留空使用内置默认 |
| `SYSTEM_PROMPT` | 两种模式都会追加的通用要求（如语言风格），可为空 |

对话接口通过 `mode` 字段选择模式：`"professional"`（默认）或 `"psychologist"`。

## 📚 知识库：把数字人变成专有领域问答助手

打开页面后展开「📚 知识库」面板，点击或拖拽文件上传即可。上传成功会立即重建索引，
下一次提问就生效，**无需重启**。

- 支持格式：`.txt` / `.md` / `.markdown` / `.csv` / `.pdf` / `.docx`，单文件上限 10MB。
- 也可以直接把文档或**整个文件夹**放进项目根目录的 `knowledge/`（支持子目录，递归扫描），
  检索前会自动检测文件增删改并重建索引。
- 首次解析 PDF 需要几秒一篇；解析结果会缓存到 `knowledge/.kb_cache.json`，之后启动和检索
  直接复用，新增/修改文件时只重新解析该文件。
- 仓库自带 `knowledge/数字人项目介绍.md` 作为示例，可以直接问它：
  「知识库支持哪些文件格式？」「这个项目怎么启动？」「用到向量数据库了吗？」
- `knowledge/` 下除示例外的文档默认被 `.gitignore` 忽略，避免私有资料误提交；
  需要公开某篇文档时用 `git add -f knowledge/文件名`。

### 工作流程（专业模式）

```
用户提问 ──► 知识库检索（中文单字/二元组 + TF-IDF 余弦相似度）
        ──► 取最相关的 Top-K 片段，拼进 system prompt
        ──► LLM 严格依据资料回答并标注引用来源
        ──► 流式分句 + TTS + 口型动画
```

专业模式始终严格：检索不到相关内容时，模型会明确回答「知识库中没有找到相关信息」，
不会用自身知识编造。心理医生模式完全不检索知识库。

### 配置项（`.env`）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `KB_ENABLED` | `true` | 是否启用知识库 |
| `KB_DIR` | `knowledge` | 文档目录，相对路径基于项目根目录 |
| `KB_TOP_K` | `4` | 每次注入的片段数（专业模式至少取 6 段） |
| `KB_MIN_SCORE` | `0.05` | 相关度阈值（0~1），低于该值视为未命中 |
| `KB_MAX_CONTEXT_CHARS` | `3000` | 注入上下文的字符上限（专业模式至少 4000） |

### 知识库 API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/kb` | 文档列表与片段统计 |
| `POST` | `/api/kb/upload` | 上传文档（multipart，字段名 `file`） |
| `DELETE` | `/api/kb/{doc_id}` | 删除文档 |
| `POST` | `/api/kb/search` | 检索调试：查看某个问题命中的片段 |
| `POST` | `/api/kb/reload` | 手动重建索引 |

### 为什么不用向量数据库？

本项目定位是「普通电脑即可运行」：TF-IDF 检索纯 Python 实现、零额外服务、毫秒级响应，
对几十篇文档的小型知识库已经够用；大规模语料再替换为 embedding + 向量库即可，
编排层只依赖 `search()` 接口，替换成本很低。

## 📄 PDF 转文字

页面上展开「📄 PDF 转文字」面板，点击或拖拽 PDF 即可提取纯文本：

- 提取后可一键**复制**，或**下载为 `.txt`**。
- 中文 PDF 常见的字间空格（如「剧  情  文  库」）会自动清理，预览更易读。
- 单文件上限 20MB；超长文本预览只显示前 3 万字，复制/下载仍是完整内容。
- 扫描版/图片型 PDF 没有文字层，会提示需要 OCR（本项目暂未内置 OCR）。

对应接口：`POST /api/pdf/extract`（multipart，字段名 `file`），
返回 `{filename, text, chars, pages}`。

### 安全说明

本项目是本地演示，**没有鉴权**。`--host 0.0.0.0` 会把对话接口和你的 API Key 额度暴露给局域网，
默认请用 `--host 127.0.0.1`；需要公网部署时请在前面加鉴权与限流。

### 运行测试

```bash
pip install pytest httpx
pytest -q
```

测试用 fake pipeline 覆盖流式协议：正常逐句事件、生成中途异常必须变成 `error` 事件、
请求体角色/长度校验、STT 非法音频返回 4xx。

## 📁 目录结构

```
digital-human/
├── backend/
│   ├── main.py             # FastAPI 入口 + 对话/知识库/PDF API + 托管前端
│   ├── pipeline.py         # 编排层：知识库检索 → 提示词注入 → 流式回复
│   ├── knowledge.py        # 知识库与 PDF 解析：多格式解析 / 切片 / TF-IDF 检索
│   ├── config.py           # 配置管理
│   └── providers/          # 可插拔 Provider 层
│       ├── llm.py          #   MockLLMProvider / DeepSeekProvider
│       ├── tts.py          #   EdgeTTSProvider
│       └── stt.py          #   FasterWhisperSTTProvider
├── frontend/
│   └── index.html          # 数字人形象 + 口型动画 + 对话/知识库界面
├── knowledge/              # 知识库文档目录（含示例文档）
├── tests/                  # 流式协议、请求校验、知识库与 PDF 转文字测试
├── requirements.txt
└── .env.example
```

## 🗺️ Roadmap

- [x] 文字对话 + TTS 语音 + 口型动画
- [x] 语音输入（STT）：麦克风说话 → 数字人回应
- [x] 流式输出：LLM 流式生成 + 分句 TTS，降低首字延迟
- [x] RAG 知识库：上传文档，让数字人回答特定领域问题
- [x] 双模式：专业问答「知语」/ 心理陪伴「心语」
- [ ] 声音克隆 / 更多音色
- [ ] 3D 形象（Live2D / 3D 模型）替代 2D SVG
- [ ] 打断功能：用户插话时中断当前回复
- [ ] 向量检索（embedding）替换词法检索，支持更大规模知识库

## 📄 License

MIT
