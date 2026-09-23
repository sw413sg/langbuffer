@echo off
setlocal
cd /d "%~dp0"
if not exist "runtime\pythonw.exe" goto missing
start "" "%~dp0runtime\pythonw.exe" -B -m live_translate
exit /b 0
:missing
echo Falta runtime\pythonw.exe. Revisa README.md.
pause
exit /b 1
