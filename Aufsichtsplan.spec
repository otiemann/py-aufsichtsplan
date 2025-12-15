# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec für die Aufsichtsplan Desktop-App.

Die Standard-Konfiguration von PyInstaller bündelt die nativen Erweiterungen
von OR-Tools (insbesondere ``cp_model_helper``) nicht automatisch. Dadurch
schlägt der Aufruf des CP-SAT-Solvers im gebauten EXE fehl und im Log erscheint
``ImportError: DLL load failed while importing cp_model_helper``. Die folgenden
Erweiterungen sorgen dafür, dass Templates/Static-Dateien, alle benötigten
FastAPI/UVicorn-Submodule sowie die OR-Tools-DLLs korrekt in den Onefile-Build
übernommen werden.
"""

from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules

# FastAPI benötigt Templates + static Assets
datas = [
    ('app/templates', 'app/templates'),
    ('app/static', 'app/static'),
]

# Versteckte Abhängigkeiten von ASGI-Stack und OR-Tools
hiddenimports = []
hiddenimports += collect_submodules('uvicorn')
hiddenimports += collect_submodules('fastapi')
hiddenimports += collect_submodules('starlette')
hiddenimports += collect_submodules('jinja2')
hiddenimports += collect_submodules('sqlalchemy')
hiddenimports += collect_submodules('pydantic')
hiddenimports += ['uvicorn.workers.uvicorn_worker']
hiddenimports += ['uvicorn.lifespan.on', 'uvicorn.lifespan.off']
hiddenimports += ['uvicorn.protocols.websockets.auto', 'uvicorn.protocols.http.auto']
hiddenimports += ['uvicorn.loops.auto']
hiddenimports += ['ortools.sat.python.cp_model_helper']

# Sammelt alle benötigten nativen Bibliotheken aus ortools (inkl. cp_model_helper)
binaries = collect_dynamic_libs('ortools')

a = Analysis(
    ['start.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Aufsichtsplan',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    onefile=True,
)
