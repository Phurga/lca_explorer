@echo off
rem First-time setup on Windows: double-click this file, or drag your ecoinvent .7z onto it.
rem Finds conda (or offers to install Miniforge), then runs scripts\bootstrap.py, which sets
rem up the Python environment and the data.
setlocal
cd /d "%~dp0"
if not defined MINIFORGE_DIR set "MINIFORGE_DIR=%USERPROFILE%\miniforge3"
set "BOOT_PY="
call :find_conda_python
if not defined BOOT_PY call :offer_miniforge
if not defined BOOT_PY call :find_any_python
if not defined BOOT_PY (
  echo No Python found. Install Miniforge from https://conda-forge.org/download/
  echo then run setup.cmd again.
  pause
  exit /b 1
)
"%BOOT_PY%" scripts\bootstrap.py %*
if errorlevel 1 pause
exit /b

:find_conda_python
rem conda's base Python: from there the setup also finds conda
if defined CONDA_EXE for %%i in ("%CONDA_EXE%\..\..") do if exist "%%~fi\python.exe" (set "BOOT_PY=%%~fi\python.exe" & exit /b)
for /f "delims=" %%c in ('where conda 2^>nul') do for %%i in ("%%~dpc..") do if exist "%%~fi\python.exe" (set "BOOT_PY=%%~fi\python.exe" & exit /b)
for %%d in ("%MINIFORGE_DIR%" "%USERPROFILE%\miniconda3" "%USERPROFILE%\anaconda3" "%LOCALAPPDATA%\miniforge3" "%LOCALAPPDATA%\miniconda3" "%LOCALAPPDATA%\anaconda3" "%ProgramData%\miniforge3" "%ProgramData%\miniconda3" "%ProgramData%\anaconda3") do if exist "%%~d\python.exe" (set "BOOT_PY=%%~d\python.exe" & exit /b)
exit /b

:offer_miniforge
echo conda was not found on this computer. lca_viewer needs it to install Python and its packages.
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
