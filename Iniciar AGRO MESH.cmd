@echo off
setlocal
cd /d "%~dp0"
set "AGRO_PY=%~dp0.venv\Scripts\python.exe"
if not exist "%AGRO_PY%" set "AGRO_PY=%~dp0..\..\work\venv\Scripts\python.exe"
if exist "%AGRO_PY%" goto run
python -m venv .venv
if errorlevel 1 goto failed
set "AGRO_PY=%~dp0.venv\Scripts\python.exe"
"%AGRO_PY%" -m pip install -r requirements.txt
if errorlevel 1 goto failed
:run
"%AGRO_PY%" launch.py
if not errorlevel 1 exit /b 0
:failed
echo.
echo AGRO MESH no pudo iniciarse. Revisa el error arriba.
pause
exit /b 1
