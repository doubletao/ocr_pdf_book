@echo off
rem Windows entry: prefer project venv, fall back to python on PATH
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  set "PY=.venv\Scripts\python.exe"
) else (
  set "PY=python"
)
"%PY%" -m book_harness.cli %*
exit /b %ERRORLEVEL%
