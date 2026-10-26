@echo off
cd /d "%~dp0"
echo [1/2] Installing PyInstaller...
python -m pip install --disable-pip-version-check --upgrade "pyinstaller>=6.10"
if errorlevel 1 goto fail
echo [2/2] Building Jumper.exe...
python -m PyInstaller --onefile --clean --noconfirm --name Jumper jumper.py
if errorlevel 1 goto fail
copy /y dist\Jumper.exe Jumper.exe >nul
rmdir /s /q build
rmdir /s /q dist
del /q Jumper.spec
echo.
echo DONE: Jumper.exe is in this folder.
pause
exit /b 0
:fail
echo.
echo BUILD FAILED. Send the error text above to Claude.
pause
exit /b 1
