@echo off
setlocal

REM Python venv optional - bevorzuge 3.12/3.11 (OR-Tools Kompatibilität)
where py >nul 2>nul && (
  py -3.12 --version >nul 2>nul && set PY=py -3.12 || (
    py -3.11 --version >nul 2>nul && set PY=py -3.11 || set PY=py
  )
) || set PY=python

%PY% -m pip install --upgrade pip
%PY% -m pip install -r requirements.txt pyinstaller

REM Clean dist
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build

REM Use spec file for consistent builds
pyinstaller Aufsichtsplan.spec

echo Build abgeschlossen. EXE unter dist\Aufsichtsplan.exe
endlocal
