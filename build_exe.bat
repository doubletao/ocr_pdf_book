@echo off
rem Build a portable Windows exe with PyInstaller (ASCII-only comments on purpose)
setlocal enabledelayedexpansion
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
  set "PY=.venv\Scripts\python.exe"
) else (
  echo [ERROR] .venv not found. Create it first: py -3.12 -m venv .venv ^&^& run.cmd setup
  exit /b 1
)

echo [1/3] Checking PyInstaller...
"%PY%" -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
  echo       Installing PyInstaller...
  "%PY%" -m pip install -U pyinstaller
  if errorlevel 1 exit /b 1
)

rem paddlex checks deps via importlib.metadata at runtime; frozen builds must
rem carry every dist-info or create_pipeline fails with DependencyError.
set "META_ARGS="
for %%D in (paddlex paddleocr paddlepaddle aistudio-sdk chardet colorlog filelock huggingface-hub modelscope numpy packaging pandas pillow prettytable py-cpuinfo pydantic PyYAML requests ruamel.yaml typing-extensions ujson beautifulsoup4 einops ftfy imagesize Jinja2 latex2mathml lxml opencv-contrib-python openpyxl premailer pyclipper pypdfium2 python-bidi regex safetensors scikit-learn scipy sentencepiece shapely tiktoken tokenizers) do (
  set "META_ARGS=!META_ARGS! --copy-metadata %%D"
)

echo [2/3] Building onedir bundle (5-15 min, paddle is huge)...
"%PY%" -m PyInstaller --noconfirm --clean --onedir --console ^
  --name BookScan2EPUB --paths . ^
  --collect-all paddle --collect-all paddlex --collect-all paddleocr ^
  --collect-all safetensors --collect-all pypdfium2 ^
  !META_ARGS! ^
  pack_entry.py
if errorlevel 1 (
  echo [ERROR] PyInstaller build failed, see build output above.
  exit /b 1
)

echo [3/3] Copying config.json next to the exe...
copy /y config.json "dist\BookScan2EPUB\config.json" >nul

echo.
echo ============================================================
echo  Done: dist\BookScan2EPUB\BookScan2EPUB.exe
echo  - No args: launches the GUI. CLI args pass through.
echo  - First run downloads models to %%USERPROFILE%%\.paddlex (~1.9GB)
echo    if not already cached.
echo ============================================================
exit /b 0
