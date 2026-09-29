@echo off
cd /d "%~dp0"
py ..\universal_adapter.py
if errorlevel 1 (
  echo.
  echo Adapter exited with an error.
  pause
)
