@echo off
setlocal
set "PROJECT_ROOT=%~dp0"
if not exist "%PROJECT_ROOT%.venv" (
  echo gossh error: virtual environment is missing. Run setup.bat first. 1>&2
  exit /b 2
)
uv run --project "%PROJECT_ROOT%" --no-sync python -m app --app-config "%PROJECT_ROOT%config.toml" %*
