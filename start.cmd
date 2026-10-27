@echo off
rem Start the explorer on Windows (after setup.cmd) and open it in the browser. Stop with Ctrl+C.
setlocal
cd /d "%~dp0"
if not exist data\python.txt (
  echo Run setup.cmd first.
  pause
  exit /b 1
)
set /p PY=<data\python.txt
rem A conda environment that is not activated needs its DLL folders on PATH
for %%i in ("%PY%") do set "PREFIX=%%~dpi"
set "PATH=%PREFIX%;%PREFIX%Library\bin;%PREFIX%Scripts;%PATH%"
"%PY%" -m lca_viewer.cli serve --open %*
if errorlevel 1 pause
