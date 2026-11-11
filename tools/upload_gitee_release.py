"""上传 AI 数字人便携包到 Gitee Release。

前置条件：
1. 已把代码 push 到 Gitee（master）。
2. 设置环境变量 GITEE_TOKEN（Gitee 私人令牌，需有仓库/发行版权限）。
3. 已构建 dist/ai-digital-human-windows.zip 和 launcher-diagnose-fix 补丁。

用法（Windows cmd）：
    set GITEE_TOKEN=你的私人令牌
    .venv\\Scripts\\python.exe tools\\upload_gitee_release.py

PowerShell：
    $env:GITEE_TOKEN="你的私人令牌"
    .venv\\Scripts\\python.exe tools\\upload_gitee_release.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import requests

API = "https://gitee.com/api/v5"
REPO = os.getenv("GITEE_REPO", "zjlhslyjsf0036/ai-digital-human")
TAG = os.getenv("GITEE_TAG", "v1.0.1")
ROOT = Path(__file__).resolve().parents[1]
FILES = [
    ROOT / "dist" / "ai-digital-human-windows.zip",
    ROOT / "dist" / "launcher-diagnose-fix-v1.0.1.zip",
]

NOTES = """## AI 数字人 Windows 免安装版 v1.0.1

- 下载 `ai-digital-human-windows.zip`，解压后双击 **start.bat**（或 启动数字人.bat），
  不需要安装 Python。
- 首次使用按页面提示填写自己的 DeepSeek API Key（只保存在本机 .env，不会上传）。
- 已经下载 v1.0.0 的用户：下载 `launcher-diagnose-fix-v1.0.1.zip` 覆盖到原文件夹即可。

### 本次更新

- 新增页面「使用指南」提示助手和 `使用指南.md`
- 启动器增加完整解压自检、ASCII 入口 `start.bat`、端口自动选择
- 新增 `diagnose.bat` 诊断日志和 `logs/app.log`
- 新增 `BUGLOG.md` 问题记录与反馈模板
- 精简包体，适配 Gitee 100MB 附件限制

首次说话会下载 Faster-Whisper 模型（约 150MB），需要联网；DeepSeek 和 Edge-TTS
需要联网，知识库与上下文记忆都在本地。包内不包含 API Key、私有文档或聊天记录。
"""


def find_release(session: requests.Session, token: str) -> dict | None:
    resp = session.get(
        f"{API}/repos/{REPO}/releases/tags/{TAG}",
        params={"access_token": token},
        timeout=30,
    )
    if resp.status_code == 200:
        return resp.json()
    return None


def create_release(session: requests.Session, token: str) -> dict:
    resp = session.post(
        f"{API}/repos/{REPO}/releases",
        params={"access_token": token},
        data={
            "tag_name": TAG,
            "name": "AI 数字人 Windows 便携版 v1.0.1",
            "body": NOTES,
            "target_commitish": "master",
        },
        timeout=60,
    )
    if resp.status_code not in (200, 201):
        raise SystemExit(
            f"创建 Gitee Release 失败：HTTP {resp.status_code}\n{resp.text[:500]}"
        )
    return resp.json()


def main() -> None:
    token = os.getenv("GITEE_TOKEN", "").strip()
    if not token:
        raise SystemExit(
            "请先设置环境变量 GITEE_TOKEN（不要把它写进代码或提交到仓库）"
        )

    session = requests.Session()
    release = find_release(session, token)
    if release is None:
        print(f"创建 Release {TAG} ...")
        release = create_release(session, token)
    release_id = release.get("id")
    print("Release:", release.get("html_url") or release.get("url") or release_id)

    existing = {
        item.get("name")
        for item in (release.get("assets") or release.get("attach_files") or [])
    }
    for path in FILES:
        if not path.exists():
            print(f"跳过不存在的文件：{path}")
            continue
        if path.name in existing:
            print(f"已存在，跳过：{path.name}")
            continue
        size_mb = path.stat().st_size / (1024 * 1024)
        print(f"上传 {path.name}（{size_mb:.1f} MiB）...")
        with path.open("rb") as fh:
            resp = session.post(
                f"{API}/repos/{REPO}/releases/{release_id}/attach_files",
                params={"access_token": token},
                files={"file": (path.name, fh)},
                timeout=1800,
            )
        if resp.status_code not in (200, 201):
            print(f"上传失败：{path.name} HTTP {resp.status_code}")
            print(resp.text[:500])
            continue
        print(f"已上传：{path.name}")

    print("完成。请打开上面的 Release 页面确认附件。")


if __name__ == "__main__":
    main()
