@echo off
rem Windows half of lca_explorer.cmd: finds a Python (the app's own once it is set up, else conda's,
rem offering to install Miniforge), then scripts\launch.py sets up if needed and starts the app.
setlocal
cd /d "%~dp0.."
if not defined MINIFORGE_DIR set "MINIFORGE_DIR=%USERPROFILE%\miniforge3"
set "BOOT_PY="
if exist data\python.txt set /p BOOT_PY=<data\python.txt
if defined BOOT_PY if not exist "%BOOT_PY%" set "BOOT_PY="
if not defined BOOT_PY call :find_conda_python
if not defined BOOT_PY call :offer_miniforge
if not defined BOOT_PY call :find_any_python
if not defined BOOT_PY (
  echo No Python found. Install Miniforge from https://conda-forge.org/download/
  echo then run lca_explorer.cmd again.
  pause
  exit /b 1
)
rem A conda environment that is not activated needs its DLL folders on PATH
for %%i in ("%BOOT_PY%") do set "PREFIX=%%~dpi"
set "PATH=%PREFIX%;%PREFIX%Library\bin;%PREFIX%Scripts;%PATH%"
"%BOOT_PY%" scripts\launch.py %*
if errorlevel 1 pause
exit /b

:find_conda_python
rem conda's base Python: from there the setup also finds conda
if defined CONDA_EXE for %%i in ("%CONDA_EXE%\..\..") do if exist "%%~fi\python.exe" (set "BOOT_PY=%%~fi\python.exe" & exit /b)
for /f "delims=" %%c in ('where conda 2^>nul') do for %%i in ("%%~dpc..") do if exist "%%~fi\python.exe" (set "BOOT_PY=%%~fi\python.exe" & exit /b)
for %%d in ("%MINIFORGE_DIR%" "%USERPROFILE%\miniconda3" "%USERPROFILE%\anaconda3" "%LOCALAPPDATA%\miniforge3" "%LOCALAPPDATA%\miniconda3" "%LOCALAPPDATA%\anaconda3" "%ProgramData%\miniforge3" "%ProgramData%\miniconda3" "%ProgramData%\anaconda3") do if exist "%%~d\python.exe" (set "BOOT_PY=%%~d\python.exe" & exit /b)
exit /b

:offer_miniforge
echo conda was not found on this computer. LCA Explorer needs it to install Python and its packages.
echo The setup can install Miniforge (free; conda from conda-forge) in %MINIFORGE_DIR%.
echo It needs no admin rights and leaves your other software as it is.
echo.
set "ANSWER=y"
set /p "ANSWER=Install Miniforge now? [Y/n] "
if /i "%ANSWER:~0,1%"=="n" exit /b
call scripts\install_miniforge.cmd
if exist "%MINIFORGE_DIR%\python.exe" set "BOOT_PY=%MINIFORGE_DIR%\python.exe"
exit /b

:find_any_python
rem A Python 3.10+ already installed (the Microsoft Store placeholder prints nothing, so it is skipped)
for /f "delims=" %%p in ('py -3 -c "import sys; print(sys.executable) if sys.version_info >= (3, 10) else None" 2^>nul') do (set "BOOT_PY=%%p" & exit /b)
for /f "delims=" %%p in ('python -c "import sys; print(sys.executable) if sys.version_info >= (3, 10) else None" 2^>nul') do (set "BOOT_PY=%%p" & exit /b)
exit /b
