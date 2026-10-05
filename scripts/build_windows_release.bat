@echo off
setlocal
cd /d "%~dp0"

:: Switch to repository root (parent of scripts)
pushd "%~dp0..\"

echo Installing/updating build tools...
py -m pip install --upgrade pyinstaller pyserial vgamepad
if errorlevel 1 goto :fail

echo.
echo Building EXE...
py -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --onefile ^
  --windowed ^
  --name "Spektrum Universal Adapter" ^
  --collect-all vgamepad ^
  --collect-all pygame ^
  --hidden-import serial.tools.list_ports_windows ^
  "universal_adapter.py"

if errorlevel 1 goto :fail

if exist release rmdir /s /q release
mkdir release
copy /y "dist\Spektrum Universal Adapter.exe" "release\Spektrum Universal Adapter.exe" >nul
copy /y "docs\API.md" "release\API.md" >nul
copy /y "docs\README.md" "release\README.md" >nul

mkdir "release\plugins"
xcopy /y /e /i "plugins\*" "release\plugins\" >nul

mkdir "release\profiles"
xcopy /y /e /i "profiles\*" "release\profiles\" >nul

echo.
echo Finished:
echo   %CD%\release\Spektrum Universal Adapter.exe
echo.
echo The release\plugins and release\profiles folders remain editable.
pause
popd
exit /b 0

:fail
popd
echo.
echo Build failed. Copy the error text and send it to ChatGPT.
pause
exit /b 1
