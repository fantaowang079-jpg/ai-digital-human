"""构建 Windows 免安装便携包。

用法（Windows）：
    .venv\\Scripts\\python.exe tools\\build_portable.py

产物：
    dist/ai-digital-human-windows.zip

包内自带 Python 3.12 embeddable 与全部运行依赖，对方解压后双击
start.bat（或 启动数字人.bat）即可，不需要安装 Python；首次打开按页面提示填写
自己的 DeepSeek API Key。
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
setlocal EnableExtensions
cd /d "%~dp0"

if not exist "python\python.exe" goto missing
if not exist "backend\main.py" goto missing
if not exist "run_server.bat" goto missing
if not exist "frontend\index.html" goto missing

if not exist ".env" copy /y ".env.example" ".env" >nul 2>&1
if not exist "logs" mkdir "logs" >nul 2>&1

set "PORT=8000"
:findport
netstat -ano | findstr /R /C:":%PORT% " | findstr /I "LISTENING" >nul 2>&1
if errorlevel 1 goto portfound
set /a PORT+=1
if %PORT% GTR 8010 goto noport
goto findport

:portfound
echo Starting AI Digital Human on port %PORT% ...
start "AI Digital Human Server" "%~dp0run_server.bat"
timeout /t 4 /nobreak >nul 2>&1
start "" "http://localhost:%PORT%"
echo.
echo ==========================================
echo   AI Digital Human
echo   Browser: http://localhost:%PORT%
echo   Close the server window to stop.
echo ==========================================
echo.
pause
exit /b 0

:missing
echo.
echo ERROR: Required files are missing.
echo Please EXTRACT THE WHOLE ZIP first, then run this file.
echo Do NOT run it directly inside the zip preview.
echo If antivirus removed files, restore them or whitelist this folder.
echo.
echo Current folder: %CD%
if not exist "python\python.exe" echo   MISSING: python\python.exe
if not exist "backend\main.py" echo   MISSING: backend\main.py
if not exist "run_server.bat" echo   MISSING: run_server.bat
if not exist "frontend\index.html" echo   MISSING: frontend\index.html
echo.
echo Run diagnose.bat and send the logs folder to the developer.
echo Opening SOLUTION.txt with download links and setup steps...
start "" "%~dp0SOLUTION.txt"
pause
exit /b 1

:noport
echo.
echo ERROR: Ports 8000-8010 are all in use.
echo Close other programs and try again.
echo Opening SOLUTION.txt with download links and setup steps...
start "" "%~dp0SOLUTION.txt"
pause
exit /b 1
"""

RUN_SERVER = r"""@echo off
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
if not defined PORT set "PORT=8000"
if not exist "logs" mkdir "logs" >nul 2>&1
echo AI Digital Human server is starting on port %PORT% ...
echo Keep this window open. Press Ctrl+C to stop.
echo.
"%~dp0python\python.exe" -m uvicorn backend.main:app --host 127.0.0.1 --port %PORT%
echo.
echo Server stopped. Check logs\app.log for details.
pause
"""

DIAGNOSE = r"""@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
if not exist "logs" mkdir "logs" >nul 2>&1
set "OUT=%~dp0logs\diagnose.txt"
> "%OUT%" 2>&1 (
  echo AI Digital Human diagnostic report
  echo Date: %DATE% %TIME%
  echo Folder: %CD%
  echo.
  echo [Files]
  if exist "python\python.exe" (echo python\python.exe: OK) else (echo python\python.exe: MISSING)
  if exist "backend\main.py" (echo backend\main.py: OK) else (echo backend\main.py: MISSING)
  if exist "frontend\index.html" (echo frontend\index.html: OK) else (echo frontend\index.html: MISSING)
  if exist "run_server.bat" (echo run_server.bat: OK) else (echo run_server.bat: MISSING)
  if exist "start.bat" (echo start.bat: OK) else (echo start.bat: MISSING)
  if exist ".env" (echo .env: present) else (echo .env: missing)
  echo.
  echo [Python]
  if exist "python\python.exe" ("python\python.exe" --version) else (echo Python: MISSING)
  echo.
  echo [Listening ports 8000-8010]
  for %%P in (8000 8001 8002 8003 8004 8005 8006 8007 8008 8009 8010) do (
    netstat -ano | findstr /R /C:":%%P " | findstr /I "LISTENING"
  )
  echo.
  echo [App log]
  if exist "logs\app.log" (
    echo logs\app.log is present. Please send the whole logs folder.
  ) else (
    echo app.log: not found
  )
)
type "%OUT%"
echo.
echo Report saved to: %OUT%
echo Send the whole logs folder to the developer.
pause
"""

INSTRUCTIONS = """AI 数字人 Windows 便携版
========================

【第一次使用】
1. 把整个 zip 完整解压到桌面或文档目录（不要放 Program Files）。
   - 不要直接在压缩包预览里双击 .bat。
2. 进入解压后的文件夹，双击 start.bat（或 启动数字人.bat）。
3. 浏览器会自动打开；如果没打开，按黑窗口显示的地址手动访问，
   通常是 http://localhost:8000 。
4. 首次使用按页面提示粘贴自己的 DeepSeek API Key：
   - Key 只保存在本机 .env 文件里，不会上传。
   - 没有 Key 可以点「先试用 Mock 模式」，界面、语音和知识库仍可体验。
   - 获取 Key：https://platform.deepseek.com/api_keys
5. 关闭黑色服务器窗口即可停止服务。
6. 首次说话会下载 Faster-Whisper 语音识别模型（约 150MB），需要联网。
7. 需要联网：DeepSeek 对话、Edge-TTS 语音合成；知识库与上下文记忆都在本地。
8. 如果 Windows 提示「未知发布者」，这是本地脚本，选择仍要运行即可。

【遇到问题】
- 提示「系统找不到指定的路径」：
  1) 确认已经完整解压，而不是在 zip 里直接运行；
  2) 确认 python\\python.exe、backend\\main.py、run_server.bat 存在；
  3) 杀毒软件可能隔离了文件，恢复或加入白名单后重新解压；
  4) 双击 diagnose.bat，把生成的 logs 文件夹发回。
- 页面显示「后端未连接」：看服务器黑窗口是否还在；看 logs\\app.log。
- 端口被占用：启动器会自动尝试 8000~8010，以窗口显示的端口为准。
- 首次说话没反应：需要联网下载语音识别模型（约 150MB），稍等。
- Windows 提示未知发布者：本地脚本没有签名，选择「仍要运行」。

【日志】
- logs\\diagnose.txt：环境/文件/端口诊断
- logs\\app.log：应用错误日志
- 反馈问题时请附上 logs 文件夹，但不要发送 .env（里面有 Key）。

【下载与更多帮助】
- Gitee 发行版：https://gitee.com/zjlhslyjsf0036/ai-digital-human/releases
- 详细解决方案：同目录 SOLUTION.txt / 使用指南.md / BUGLOG.md
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
            "pip-",
            "pytest",
            "_pytest",
            "pluggy",
            "iniconfig",
            "pygments",
        )
        # 注意：不能跳过 httpx2/httpcore2/truststore/hf_xet，
        # huggingface_hub 下载 Whisper 模型需要它们。
        skip_exact = {"pip", "pygments", "pluggy", "iniconfig", "httpx", "httpcore"}
        skip_prefixes_2 = ("httpx-", "httpcore-")
        for item in venv_site.iterdir():
            name = item.name
            if name.startswith(skip_prefixes) or name in skip_exact:
                continue
            if name.startswith(skip_prefixes_2):
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

    # 删掉运行时用不到的测试/文档，减小分发包（尤其为了适配 Gitee 附件大小）
    print("[prune] 清理测试与文档")
    for pattern in (
        "numpy/**/tests",
        "numpy/**/test",
        "numpy/doc",
        "certifi/tests",
        "onnxruntime/test",
        "onnxruntime/tests",
    ):
        for path in site_packages.glob(pattern):
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
    for path in site_packages.glob("av/**/*.pyx"):
        path.unlink(missing_ok=True)
    for path in site_packages.glob("av/**/*.pxd"):
        path.unlink(missing_ok=True)
    for pattern in ("numpy/**/include", "numpy/**/*.pyi", "av/**/*.pyi"):
        for path in site_packages.glob(pattern):
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
    # 这些是可选加速/开发依赖，删掉不影响 HTTP 服务；
    # hf_xet 必须保留：huggingface_hub 模型下载依赖它。
    # onnxruntime 只用于 Whisper 的 VAD 过滤，本项目 VAD 在前端，
    # 且 transcribe 未开启 vad_filter，可以安全移除（已实测识别正常）。
    for name in ("watchfiles", "websockets", "onnxruntime"):
        for path in site_packages.glob(f"{name}*"):
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)

    print("[copy] 应用代码")
    copytree(ROOT / "backend", PKG / "backend")
    copytree(ROOT / "frontend", PKG / "frontend")
    for name in (
        "README.md",
        ".env.example",
        "requirements.txt",
        "LICENSE",
        "BUGLOG.md",
        "使用指南.md",
        "SOLUTION.txt",
    ):
        source = ROOT / name
        if source.exists():
            shutil.copy2(source, PKG / name)

    # 只带示例知识库，绝不打包用户自己的文档
    knowledge = PKG / "knowledge"
    knowledge.mkdir(exist_ok=True)
    sample = ROOT / "knowledge" / "数字人项目介绍.md"
    if sample.exists():
        shutil.copy2(sample, knowledge / sample.name)

    write_windows_text(PKG / "start.bat", LAUNCHER)
    write_windows_text(PKG / "启动数字人.bat", LAUNCHER)
    write_windows_text(PKG / "run_server.bat", RUN_SERVER)
    write_windows_text(PKG / "diagnose.bat", DIAGNOSE)
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
