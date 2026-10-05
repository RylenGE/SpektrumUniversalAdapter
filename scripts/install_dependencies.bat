@echo off
cd /d "%~dp0"
py -m pip install -r ..\requirements.txt
echo.
echo Installation complete.
pause
