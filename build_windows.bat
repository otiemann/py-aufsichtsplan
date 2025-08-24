@echo off
setlocal

REM Python venv optional - priorisiere neueste Version
where py >nul 2>nul && (
  REM Prüfe nach Python 3.13, 3.12, dann Fallback zu py
  py -3.13 --version >nul 2>nul && set PY=py -3.13 || (
    py -3.12 --version >nul 2>nul && set PY=py -3.12 || set PY=py
  )
) || set PY=python

%PY% -m pip install --upgrade pip
%PY% -m pip install -r requirements.txt pyinstaller

REM Clean dist
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build

REM Bundle with templates and static
pyinstaller --noconsole --onefile ^
  --name Aufsichtsplan ^
  --add-data "app\templates;app\templates" ^
  --add-data "app\static;app\static" ^
  start.py

echo Build abgeschlossen. EXE unter dist\Aufsichtsplan.exe
endlocal

