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

REM Use spec file for consistent builds, fallback to command-line parameters
if exist "Aufsichtsplan.spec" (
    echo Using spec file...
    pyinstaller Aufsichtsplan.spec
) else (
    echo Using fallback build method...
    pyinstaller --onefile --noconsole ^
      --name Aufsichtsplan ^
      --add-data "app/templates;app/templates" ^
      --add-data "app/static;app/static" ^
      --hidden-import uvicorn.workers.uvicorn_worker ^
      --hidden-import uvicorn.lifespan.on ^
      --hidden-import uvicorn.lifespan.off ^
      --hidden-import uvicorn.protocols.websockets.auto ^
      --hidden-import uvicorn.protocols.http.auto ^
      --hidden-import uvicorn.loops.auto ^
      start.py
)

echo Build abgeschlossen. EXE unter dist\Aufsichtsplan.exe
endlocal

