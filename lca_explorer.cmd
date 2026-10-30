:; exec sh "$(dirname "$0")/scripts/launch.sh" "$@"
@call "%~dp0scripts\launch.cmd" %*
@exit /b %errorlevel%
