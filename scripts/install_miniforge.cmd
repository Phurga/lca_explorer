@echo off
rem Install Miniforge (conda from conda-forge) for the current user, called by setup.cmd.
rem No admin rights needed; PATH and the default Python are left unchanged.
rem Installs into %MINIFORGE_DIR%, or %USERPROFILE%\miniforge3.
setlocal
if not defined MINIFORGE_DIR set "MINIFORGE_DIR=%USERPROFILE%\miniforge3"
set "INSTALLER=%TEMP%\Miniforge3-Windows-x86_64.exe"
echo Downloading Miniforge (about 90 MB)...
curl.exe -L --fail --progress-bar -o "%INSTALLER%" https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Windows-x86_64.exe
if errorlevel 1 (
  echo The download failed. Check the internet connection, or install Miniforge yourself:
  echo https://conda-forge.org/download/
  exit /b 1
)
echo Installing Miniforge in %MINIFORGE_DIR% (a few minutes)...
rem /D must come last, without quotes
start /wait "" "%INSTALLER%" /InstallationType=JustMe /RegisterPython=0 /AddToPath=0 /S /D=%MINIFORGE_DIR%
del "%INSTALLER%"
if not exist "%MINIFORGE_DIR%\python.exe" (
  echo The installation failed. Install Miniforge yourself: https://conda-forge.org/download/
  exit /b 1
)
echo Miniforge installed.
