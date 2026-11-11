"""构建 Windows 免安装便携包。

用法（Windows）：
    .venv\\Scripts\\python.exe tools\\build_portable.py

产物：
    dist/ai-digital-human-windows.zip

包内自带 Python 3.12 embeddable 与全部运行依赖，对方解压后双击
「启动数字人.bat」即可，不需要安装 Python；首次打开按页面提示填写自己的
DeepSeek API Key。
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
PKG_NAME = "ai-digital-human-windows"
PKG = DIST / PKG_NAME
PY_VER = "3.12.10"
PY_ZIP_URL = (
    f"https://www.python.org/ftp/python/{PY_VER}/python-{PY_VER}-embed-amd64.zip"
)
CACHE = DIST / "cache"
OUT_ZIP = DIST / f"{PKG_NAME}.zip"

LAUNCHER = r"""@echo off
setlocal
cd /d "%~dp0"
if not exist ".env" copy /y ".env.example" ".env" >nul
echo Starting AI Digital Human...
start "AI Digital Human Server" "%~dp0run_server.bat"
timeout /t 4 /nobreak >nul
start "" http://localhost:8000
echo.
echo ==========================================
echo   AI Digital Human
echo   Browser: http://localhost:8000
echo   Close the server window to stop.
echo ==========================================
echo.
pause
"""

RUN_SERVER = r"""@echo off
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
echo AI Digital Human server is running.
echo Keep this window open. Press Ctrl+C to stop.
echo.
"%~dp0python\python.exe" -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
echo.
echo Server stopped.
pause
"""

INSTRUCTIONS = """AI 数字人 Windows 便携版
========================

1. 将整个文件夹解压到桌面或文档目录（不要放在 Program Files，可能没有写权限）。
2. 双击「启动数字人.bat」。
3. 浏览器会自动打开 http://localhost:8000 。
4. 首次使用请按页面提示粘贴自己的 DeepSeek API Key：
   - Key 只保存在本机 .env 文件里，不会上传。
   - 没有 Key 可以点「先试用 Mock 模式」，界面、语音和知识库仍可体验。
   - 获取 Key：https://platform.deepseek.com/api_keys
5. 关闭黑色服务器窗口即可停止服务。
6. 首次说话会下载 Faster-Whisper 语音识别模型（约 150MB），需要联网。
7. 需要联网：DeepSeek 对话、Edge-TTS 语音合成；知识库与上下文记忆都在本地。
8. 如果 Windows 提示「未知发布者」，这是本地脚本，选择仍要运行即可。
"""


def download(url: str, dest: Path) -> None:
    if dest.exists() and dest.stat().st_size > 0:
        print(f"[cache] {dest.name}")
        return
    print(f"[download] {url}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(url, dest)


def copytree(src: Path, dst: Path) -> None:
    shutil.copytree(
        src,
        dst,
        ignore=shutil.ignore_patterns(
            "__pycache__", "*.pyc", "*.pyo", ".pytest_cache"
        ),
    )


def write_windows_text(path: Path, text: str, encoding: str = "ascii") -> None:
    path.write_text(text.replace("\n", "\r\n"), encoding=encoding, newline="")


def main() -> None:
    if sys.version_info[:2] != (3, 12):
        print("警告：建议用 Python 3.12 运行构建脚本，保证扩展模块 ABI 一致")

    DIST.mkdir(exist_ok=True)
    CACHE.mkdir(exist_ok=True)
    if PKG.exists():
        print(f"[clean] {PKG}")
        shutil.rmtree(PKG)
    PKG.mkdir(parents=True)

    pyzip = CACHE / f"python-{PY_VER}-embed-amd64.zip"
    download(PY_ZIP_URL, pyzip)
    with zipfile.ZipFile(pyzip) as zf:
        zf.extractall(PKG / "python")

    py_dir = PKG / "python"
    pth_files = list(py_dir.glob("python*._pth"))
    if not pth_files:
        raise SystemExit("解压后的 Python 目录里找不到 ._pth 文件")
    # 让 embeddable Python 能找到 site-packages 和上层项目目录
    pth_files[0].write_text(
        "python312.zip\n.\nLib\\site-packages\n..\nimport site\n",
        encoding="utf-8",
    )

    site_packages = py_dir / "Lib" / "site-packages"
    site_packages.mkdir(parents=True, exist_ok=True)

    # 优先复用当前虚拟环境已经装好的依赖，避免重新下载几百 MB。
    venv_site = Path(sys.prefix) / "Lib" / "site-packages"
    if venv_site.is_dir() and (venv_site / "fastapi").is_dir():
        print("[copy] 复用当前虚拟环境的依赖")
        skip_prefixes = (
            "~",          # pip 中断产生的临时目录
            "pip",
            "pytest",
            "_pytest",
            "pluggy",
            "iniconfig",
            "pygments",
            "httpx",
            "httpcore",
        )
        for item in venv_site.iterdir():
            if item.name.startswith(skip_prefixes):
                continue
            target = site_packages / item.name
            if item.is_dir():
                shutil.copytree(
                    item,
                    target,
                    dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
                )
            else:
                shutil.copy2(item, target)
    else:
        print("[pip] 当前环境缺少依赖，改为在线安装（会比较慢）...")
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--target",
                str(site_packages),
                "--upgrade",
                "--disable-pip-version-check",
                "--no-warn-script-location",
                "-r",
                str(ROOT / "requirements.txt"),
            ],
            check=True,
        )

    print("[copy] 应用代码")
    copytree(ROOT / "backend", PKG / "backend")
    copytree(ROOT / "frontend", PKG / "frontend")
    for name in ("README.md", ".env.example", "requirements.txt", "LICENSE"):
        source = ROOT / name
        if source.exists():
            shutil.copy2(source, PKG / name)

    # 只带示例知识库，绝不打包用户自己的文档
    knowledge = PKG / "knowledge"
    knowledge.mkdir(exist_ok=True)
    sample = ROOT / "knowledge" / "数字人项目介绍.md"
    if sample.exists():
        shutil.copy2(sample, knowledge / sample.name)

    write_windows_text(PKG / "启动数字人.bat", LAUNCHER)
    write_windows_text(PKG / "run_server.bat", RUN_SERVER)
    write_windows_text(PKG / "使用说明.txt", INSTRUCTIONS, encoding="utf-8-sig")

    print("[zip] 压缩分发包")
    if OUT_ZIP.exists():
        OUT_ZIP.unlink()
    shutil.make_archive(
        str(OUT_ZIP.with_suffix("")),
        "zip",
        root_dir=str(DIST),
        base_dir=PKG_NAME,
    )
    size_mb = OUT_ZIP.stat().st_size / (1024 * 1024)
    print(f"[done] {OUT_ZIP} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
