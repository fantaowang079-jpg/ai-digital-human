# Gitee Release 上传说明

代码和 `使用指南.md` 已经推送到 Gitee。Gitee 的**发行版附件**需要通过 API 上传，
而 API 需要一个**私人令牌**；为了安全，项目不会读取或保存你的 Gitee token。

## 方式一：脚本上传（推荐）

1. 打开 Gitee → 设置 → 私人令牌 → 新建令牌，至少勾选仓库相关权限（`projects`）。
2. **最简单**：双击项目根目录的 **`上传到Gitee.bat`**，粘贴令牌即可。
   它会在当前窗口临时设置 `GITEE_TOKEN`，不会写入仓库或磁盘。
3. 或者在项目根目录执行（Windows cmd）：

   ```bat
   set GITEE_TOKEN=你的私人令牌
   .venv\Scripts\python.exe tools\upload_gitee_release.py
   ```

   PowerShell：

   ```powershell
   $env:GITEE_TOKEN="你的私人令牌"
   .venv\Scripts\python.exe tools\upload_gitee_release.py
   ```

3. 脚本会：
   - 创建/复用 `v1.0.1` Release
   - 上传 `dist/ai-digital-human-windows.zip`（完整便携包，约 95MiB）
   - 上传 `dist/launcher-diagnose-fix-v1.0.1.zip`（旧版覆盖补丁）

> Token 只放在当前命令行环境变量里，不会写入仓库或日志。用完可以关掉窗口。

## 方式二：网页手动上传

1. 打开 <https://gitee.com/zjlhslyjsf0036/ai-digital-human/releases/new>
2. Tag 填 `v1.0.1`，标题填「AI 数字人 Windows 便携版 v1.0.1」
3. 说明可以直接复制 [Release 说明](#release-说明) 一节
4. 上传以下两个文件：
   - `dist/ai-digital-human-windows.zip`
   - `dist/launcher-diagnose-fix-v1.0.1.zip`
5. 发布后，把 Release 链接发给用户即可。

## 附件大小

- Gitee 单个 Release 附件上限 **100MB**（GVP 项目 200MB）。
- 本项目完整包已精简到 **99,516,877 字节（约 94.9MiB）**，低于 100MB。
- 补丁包约 37KB。

## 校验值

```text
完整包  SHA256: b95a818ed9e55fd643956b955b9a39bc7747c3a31739f0dc13295f696aba3cd3
补丁包  SHA256: dc0c49716b3c0dcef80e8ff1538845aea79bdbd5d3e87db5c9d64ae6cc0bc5ce
```

## Release 说明

```markdown
## AI 数字人 Windows 免安装版 v1.0.1

- 下载 `ai-digital-human-windows.zip`，解压后双击 start.bat（或 启动数字人.bat），
  不需要安装 Python。
- 首次使用按页面提示填写自己的 DeepSeek API Key（只保存在本机 .env，不会上传）。
- 已经下载 v1.0.0 的用户：下载 launcher-diagnose-fix-v1.0.1.zip 覆盖到原文件夹即可。

### 本次更新
- 页面新增「使用指南」提示助手，新增 使用指南.md
- 启动器增加完整解压自检、ASCII 入口 start.bat、端口自动选择
- 新增 diagnose.bat 诊断日志与 logs/app.log
- 新增 BUGLOG.md 问题记录与反馈模板
- 精简包体，适配 Gitee 100MB 附件限制

首次说话需联网下载语音识别模型（约 150MB）；包内不包含 API Key、私有文档或聊天记录。
```

## 下载链接格式

发布后附件地址形如：

```text
https://gitee.com/zjlhslyjsf0036/ai-digital-human/releases/download/v1.0.1/ai-digital-human-windows.zip
```

可以把 Release 页面链接或这个直链发给用户。
