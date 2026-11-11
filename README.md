# AI 数字人对话系统

> 一个从零实现的精简 AI 数字人：**听 → 想 → 说 → 动** 全链路可运行。编排层与前端为自研实现，底层调用 DeepSeek（LLM）、Edge-TTS（语音合成）与 Faster-Whisper（语音识别），可插拔架构，不依赖高端 GPU。
>
> 📦 **Windows 用户**：可以直接下载[免安装便携版](https://github.com/fantaowang079-jpg/ai-digital-human/releases/latest)，解压后双击 `start.bat`（或 `启动数字人.bat`）即可，不需要安装 Python。
> Gitee 仓库已同步；**Gitee Release 附件需要维护者用自己的 Gitee Token 上传后才会出现**（见 [GITEE_RELEASE.md](GITEE_RELEASE.md)）。如果 Gitee 发行版页面还没有附件，请先用 GitHub Release。
>
> 📖 **不会用？** 看[使用指南](使用指南.md)；遇到问题看 [BUGLOG.md](BUGLOG.md)。
> 维护者上传 Gitee Release 见 [GITEE_RELEASE.md](GITEE_RELEASE.md)。
>
> ⚠️ **如果你下载的是源码 ZIP（文件夹名类似 `ai-digital-human-master`）**：它**不包含 Python 环境和依赖**，不能直接双击启动。只想一键运行请下载 Release 里的 `ai-digital-human-windows.zip`（解压后双击 `start.bat`）；想从源码运行请按下方「快速开始」安装依赖。
>
> 🧭 **启动失败/提示找不到路径**：看 [SOLUTION.txt](SOLUTION.txt)（源码启动脚本报错时会自动打开它），里面包含下载地址、源码安装步骤和「搜不到大模型」的说明。

前端展示一个 2D 数字人形象，支持**文字或语音**输入：后端调用大模型**流式**生成回复，按句合成语音并逐句返回，网页端边接收边播放，用音频实时驱动数字人的「嘴巴」做口型动画。

还内置**本地知识库（RAG-lite）**、**双模式人格**与**持久化上下文记忆**：网页上传自己的文档后，「专业模式 · 知语」会严格依据知识库回答并标注引用；「心理医生模式 · 心语」则只和你轻松聊天；两种模式都能记住你以前说过的话，刷新或重启后仍可接着聊。

---

## ✨ 亮点

1. **本地知识库（RAG-lite）**：上传 txt / md / csv / pdf / docx，自动切片建索引，提问时检索相关片段注入提示词；纯 Python TF-IDF 检索，无需向量数据库和 GPU。
2. **双模式对话**：专业模式「知语」严格引用知识库并展示来源；心理医生模式「心语」专注共情闲聊，两条链路互不干扰。
3. **上下文记忆**：本地 SQLite 持久化对话，长对话自动压缩成滚动摘要，提问时检索相关旧话题；刷新/重启后自动恢复最近聊天记录。
4. **逐词流式**：LLM 生成的文本逐块推送到前端，边生成边上屏；音频按句合成、逐句播放。
5. **PDF 转文字**：网页上传 PDF，提取纯文本并支持复制、下载 txt；扫描版 PDF 会提示需要 OCR。
6. **可插拔 Provider 架构**：LLM / TTS / STT 各自抽象成接口，DeepSeek / Edge-TTS / Faster-Whisper 可自由切换，改 `.env` 即可，上层编排零改动。
7. **语音对话**：麦克风录音 → 本地 Faster-Whisper 识别 → 数字人语音回复，真正的「语音数字人」。
8. **无 GPU 依赖**：LLM、TTS 走 API，口型与知识库检索在本地用轻量方案实现，普通电脑即可运行。
9. **多轮对话**：前端维护对话历史，后端按模式注入人格设定、长期记忆与知识库上下文。

## 🏗️ 架构

```
浏览器前端 (SVG 数字人 + 口型动画 + 麦克风录音)
        │  HTTP /api/chat/stream · /api/stt · /api/memory
        ▼
FastAPI 后端 (main.py)
        │
        ▼
编排层 (pipeline.py)  ── 读取长期记忆 + 检索知识库 → 注入提示词 → 逐词流式输出 → 分句 TTS
        │
        ├── 上下文记忆 (memory.py)   ← data/memory.db，最近对话 + 滚动摘要 + 相关回忆
        ├── 知识库 (knowledge.py)    ← knowledge/ 文档，TF-IDF 检索
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

## 📦 Windows 免安装版（给别人演示）

不想让对方安装 Python？构建一个自带运行环境的便携包：

```bash
.venv\Scripts\python.exe tools\build_portable.py
```

产物是 `dist/ai-digital-human-windows.zip`。对方只需要：

1. **完整解压**到桌面或文档目录（不要放在 `Program Files`，不要在压缩包预览里直接运行）。
2. 双击 `start.bat`（或 `启动数字人.bat`）。
3. 浏览器自动打开；如果没打开，按黑窗口显示的端口手动访问（通常是 `http://localhost:8000`）。
4. 首次使用按页面提示粘贴自己的 DeepSeek API Key（只保存在本机 `.env`，不会上传）；
   没有 Key 可以点「先试用 Mock 模式」体验界面和语音。

包内自带 Python 3.12 embeddable 与全部运行依赖，**不需要安装 Python**；只包含示例
知识库，不会打包你本地的文档、记忆或 API Key。首次使用麦克风会下载 Faster-Whisper
模型（约 150MB），需要联网；DeepSeek 对话和 Edge-TTS 也需要联网，知识库与记忆在本地。

**遇到「系统找不到指定的路径」**：通常是没完整解压、直接在 zip 里双击，或杀毒软件
隔离了 `python\python.exe`。先双击 `diagnose.bat`，把 `logs` 文件夹发回；常见问题
和处理方法见 [BUGLOG.md](BUGLOG.md)。

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

## 🧠 上下文记忆

数字人会记住你以前说过的话：刷新页面、重启服务后仍能接着聊。

- 每个浏览器自动生成一个 `session_id` 并存在 `localStorage`，对话按「会话 + 模式」隔离保存。
- 对话记录存在本地 SQLite：`data/memory.db`（已加入 `.gitignore`，不会上传）。
- 每次成功回复后自动写入记忆；旧对话攒够一批后由大模型压缩成滚动摘要，
  最近 12 条保持原文，避免提示词无限变长。
- 每次提问会检索与当前问题相关的旧对话，并结合最近对话回忆注入提示词；
  刷新页面后自动恢复最近 20 条聊天记录到对话区。
- 页面「🧠 上下文记忆」面板可查看摘要和最近记录，也可以一键清除当前模式的记忆。
- 记忆按模式隔离：专业模式「知语」和心理医生模式「心语」各自拥有独立的记忆。

### 记忆配置

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MEMORY_ENABLED` | `true` | 是否启用持久化记忆 |
| `MEMORY_DB_PATH` | `data/memory.db` | SQLite 路径，相对路径基于项目根目录 |
| `MEMORY_KEEP_VERBATIM` | `12` | 最近多少条消息原样保留 |
| `MEMORY_SUMMARIZE_AFTER` | `12` | 未摘要旧消息达到多少条后自动压缩 |

记忆接口：`GET /api/memory?session_id=&mode=` 查看，`DELETE /api/memory?session_id=&mode=` 清除。

### 流式协议（NDJSON）

`/api/chat/stream` 每行一个 JSON 事件：

| 事件 | 说明 |
| --- | --- |
| `{"type":"delta","text":"..."}` | LLM 新增的文本，前端逐词追加到气泡 |
| `{"type":"sentence","text":"...","audio_base64":"..."}` | 一个完整句子及其语音，前端排队播放 |
| `{"type":"sources","sources":[...]}` | 专业模式命中的知识库文件 |
| `{"type":"error","message":"..."}` | 生成中途失败 |

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
用户提问 ──► 读取长期记忆（摘要 + 相关回忆）
        ──► 知识库检索（中文单字/二元组 + TF-IDF 余弦相似度）
        ──► 取最相关的 Top-K 片段，拼进 system prompt
        ──► LLM 严格依据资料回答并标注引用来源
        ──► delta 事件逐词上屏 + sentence 事件分句 TTS/口型
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

测试用 fake pipeline 覆盖流式协议：逐词 delta + 句子音频事件、生成中途异常必须变成
`error` 事件、请求体角色/长度校验、STT 非法音频返回 4xx；另外覆盖知识库、
上下文记忆（持久化/摘要/回忆）与 PDF 转文字。

## 📁 目录结构

```
digital-human/
├── backend/
│   ├── main.py             # FastAPI 入口 + 对话/知识库/PDF/记忆 API + 托管前端
│   ├── pipeline.py         # 编排层：记忆 + 知识库检索 → 提示词注入 → 逐词流式
│   ├── memory.py           # 上下文记忆：SQLite / 滚动摘要 / 相关回忆
│   ├── knowledge.py        # 知识库与 PDF 解析：多格式解析 / 切片 / TF-IDF 检索
│   ├── config.py           # 配置管理
│   └── providers/          # 可插拔 Provider 层
│       ├── llm.py          #   MockLLMProvider / DeepSeekProvider
│       ├── tts.py          #   EdgeTTSProvider
│       └── stt.py          #   FasterWhisperSTTProvider
├── frontend/
│   └── index.html          # 数字人形象 + 口型动画 + 对话/知识库/PDF/记忆界面
├── knowledge/              # 知识库文档目录（含示例文档）
├── data/                   # 本地记忆数据库（gitignore，不提交）
├── tools/
│   ├── build_portable.py   # 构建 Windows 免安装便携包
│   └── upload_gitee_release.py  # 上传 Gitee Release（需 GITEE_TOKEN）
├── tests/                  # 流式协议、知识库、上下文记忆与 PDF 测试
├── 使用指南.md             # 面向普通用户的上手说明
├── BUGLOG.md               # 已知问题、排查步骤与反馈模板
├── GITEE_RELEASE.md        # Gitee Release 上传说明
├── requirements.txt
└── .env.example
```

## 🗺️ Roadmap

- [x] 文字对话 + TTS 语音 + 口型动画
- [x] 语音输入（STT）：麦克风说话 → 数字人回应
- [x] 流式输出：LLM 流式生成 + 分句 TTS，降低首字延迟
- [x] 逐词上屏：delta 文本事件 + sentence 音频事件
- [x] RAG 知识库：上传文档，让数字人回答特定领域问题
- [x] 双模式：专业问答「知语」/ 心理陪伴「心语」
- [x] 上下文记忆：本地持久化 + 滚动摘要 + 相关回忆检索
- [x] Windows 免安装便携包：内置 Python，解压双击即可运行
- [ ] 声音克隆 / 更多音色
- [ ] 3D 形象（Live2D / 3D 模型）替代 2D SVG
- [ ] 打断功能：用户插话时中断当前回复
- [ ] 向量检索（embedding）替换词法检索，支持更大规模知识库

## 📄 License

MIT
