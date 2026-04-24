import os
import signal
import threading
import shutil
import time
import sqlite3
import tempfile
from datetime import datetime
from fastapi import BackgroundTasks, FastAPI, Request, HTTPException, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import JSONResponse, FileResponse
from sqlalchemy import text
from starlette.concurrency import run_in_threadpool

from .database import Base, engine, SessionLocal, SQLALCHEMY_DATABASE_URL

app = FastAPI(title="Pausenaufsichtsplan")

MINIMUM_RESTORE_TABLES = {"teachers", "floors", "duty_slots", "assignments"}

# Version info import
try:
    import sys
    import os
    # Add project root to path if needed
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    from version import get_version_info
except ImportError:
    def get_version_info():
        return {"version": "unknown", "build_date": "unknown"}

RES_DIR_ENV = os.environ.get("APP_RESOURCES_DIR") or os.getcwd()

# Kandidaten für Templates- und Static-Pfade
TEMPLATE_CANDIDATES = [
	os.path.join(RES_DIR_ENV, "app", "templates"),
	os.path.join(RES_DIR_ENV, "templates"),
	os.path.join(os.path.dirname(__file__), "templates"),
]
STATIC_CANDIDATES = [
	os.path.join(RES_DIR_ENV, "app", "static"),
	os.path.join(RES_DIR_ENV, "static"),
	os.path.join(os.path.dirname(__file__), "static"),
]

TEMPLATES_DIR = next((p for p in TEMPLATE_CANDIDATES if os.path.isdir(p)), TEMPLATE_CANDIDATES[0])
STATIC_DIR = next((p for p in STATIC_CANDIDATES if os.path.isdir(p)), STATIC_CANDIDATES[0])

# Logging-Hinweis mit mehr Details
import sys
print(f"[INFO] Python ausführbar: {sys.executable}")
print(f"[INFO] Arbeitsverzeichnis: {os.getcwd()}")
print(f"[INFO] APP_RESOURCES_DIR: {RES_DIR_ENV}")
print(f"[INFO] Templates-Verzeichnis: {TEMPLATES_DIR} (existiert: {os.path.isdir(TEMPLATES_DIR)})")
print(f"[INFO] Static-Verzeichnis: {STATIC_DIR} (existiert: {os.path.isdir(STATIC_DIR)})")

# Zeige welche Templates gefunden werden
if os.path.isdir(TEMPLATES_DIR):
	try:
		templates_found = os.listdir(TEMPLATES_DIR)
		print(f"[INFO] Gefundene Templates: {templates_found}")
	except Exception as e:
		print(f"[ERROR] Fehler beim Lesen der Templates: {e}")

templates = Jinja2Templates(directory=TEMPLATES_DIR)

# Static
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.on_event("startup")
def on_startup() -> None:
    Base.metadata.create_all(bind=engine)


@app.get("/")
async def root(request: Request):
    return templates.TemplateResponse("home.html", {"request": request})


@app.get("/test/database")
async def test_database():
    """Test-Route für Datenbank-Zugriff"""
    try:
        db_path = get_db_file_path()
        return JSONResponse(content={
            "status": "success",
            "db_path": db_path,
            "db_exists": os.path.exists(db_path)
        })
    except Exception as e:
        return JSONResponse(content={
            "status": "error",
            "error": str(e)
        })



def get_db_file_path() -> str:
    """Ermittelt den tatsächlichen Pfad zur SQLite-Datenbank"""
    db_url = SQLALCHEMY_DATABASE_URL
    if db_url.startswith("sqlite:///"):
        return db_url[10:]  # Entferne "sqlite:///"
    raise ValueError("Nur SQLite-Datenbanken werden für Backup/Restore unterstützt")


def _create_sqlite_backup(source_path: str, target_path: str) -> None:
    with sqlite3.connect(source_path) as source:
        with sqlite3.connect(target_path) as target:
            source.backup(target)


def _validate_sqlite_restore_file(path: str) -> None:
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
            quick_check = conn.execute("PRAGMA quick_check").fetchone()
            if not quick_check or quick_check[0] != "ok":
                raise ValueError("SQLite quick_check fehlgeschlagen.")
            rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    except sqlite3.DatabaseError as exc:
        raise ValueError(f"Datei ist keine gültige SQLite-Datenbank: {exc}") from exc

    tables = {row[0] for row in rows}
    missing = sorted(MINIMUM_RESTORE_TABLES - tables)
    if missing:
        raise ValueError("Erwartete Tabellen fehlen: " + ", ".join(missing))


def _remove_sqlite_sidecar_files(db_path: str) -> None:
    for suffix in ("-wal", "-shm"):
        try:
            os.remove(db_path + suffix)
        except FileNotFoundError:
            pass


@app.get("/backup/download")
async def download_backup(background_tasks: BackgroundTasks):
    """Lädt die aktuelle Datenbank als Backup-Datei herunter"""
    try:
        db_path = get_db_file_path()
        
        if not os.path.exists(db_path):
            raise HTTPException(status_code=404, detail="Datenbank-Datei nicht gefunden")
        
        # Erstelle Backup-Dateinamen mit Zeitstempel
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_filename = f"aufsichtsplan_backup_{timestamp}.db"
        tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        tmp_file.close()
        _create_sqlite_backup(db_path, tmp_file.name)
        background_tasks.add_task(lambda path: os.path.exists(path) and os.remove(path), tmp_file.name)
        
        return FileResponse(
            path=tmp_file.name,
            filename=backup_filename,
            media_type="application/octet-stream"
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Backup-Fehler: {str(e)}")


@app.post("/backup/restore")
async def restore_backup(file: UploadFile = File(...)):
    """Stellt die Datenbank aus einer Backup-Datei wieder her"""
    temp_restore_path = None
    try:
        # Validiere Dateiname und -typ
        if not file.filename or not file.filename.lower().endswith('.db'):
            raise HTTPException(status_code=400, detail="Nur .db-Dateien sind erlaubt")
        
        db_path = get_db_file_path()
        db_dir = os.path.dirname(os.path.abspath(db_path)) or os.getcwd()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = f"{db_path}.backup_{timestamp}"
        
        fd, temp_restore_path = tempfile.mkstemp(prefix="aufsichtsplan_restore_", suffix=".db", dir=db_dir)
        with os.fdopen(fd, "wb") as f:
            content = await file.read()
            f.write(content)

        _validate_sqlite_restore_file(temp_restore_path)

        # Erstelle Backup der aktuellen DB und behalte es als Rückfall-Datei.
        if os.path.exists(db_path):
            try:
                _create_sqlite_backup(db_path, backup_path)
            except sqlite3.DatabaseError:
                shutil.copy2(db_path, backup_path)
        
        try:
            engine.dispose()
            _remove_sqlite_sidecar_files(db_path)
            os.replace(temp_restore_path, db_path)
            temp_restore_path = None
            
            # Teste die neue Datenbank durch eine einfache Abfrage
            with SessionLocal() as db:
                db.execute(text("SELECT 1"))
            Base.metadata.create_all(bind=engine)
            
            return JSONResponse(content={
                "message": "Datenbank erfolgreich wiederhergestellt",
                "filename": file.filename,
                "safety_backup": backup_path if os.path.exists(backup_path) else None,
            })
            
        except Exception as e:
            # Stelle die ursprüngliche Datenbank wieder her
            if os.path.exists(backup_path):
                engine.dispose()
                _remove_sqlite_sidecar_files(db_path)
                shutil.copy2(backup_path, db_path)
            raise HTTPException(status_code=400, detail=f"Ungültige Datenbank-Datei: {str(e)}")
            
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Ungültige Datenbank-Datei: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Restore-Fehler: {str(e)}")
    finally:
        if temp_restore_path and os.path.exists(temp_restore_path):
            try:
                os.remove(temp_restore_path)
            except OSError:
                pass



@app.get("/api/version")
async def get_version():
    """Gibt die aktuelle Version der Anwendung zurück"""
    try:
        version_info = get_version_info()
        return JSONResponse(content=version_info)
    except Exception as e:
        return JSONResponse(content={
            "version": "unknown", 
            "build_date": "unknown",
            "error": str(e)
        })


@app.get("/api/check-updates")
async def check_updates(demo: bool = False):
    """Prüft GitHub Releases nach verfügbaren Updates"""
    try:
        current_version_info = get_version_info()
        current_version = current_version_info.get("version", "0.0.0")
        is_packaged_app = getattr(sys, "frozen", False) and os.name == "nt"

        # Demo-Modus für Testzwecke wenn Repository privat ist
        if demo:  # Echter Update-Check aktiviert
            # Simuliere ein verfügbares Update
            from datetime import datetime
            return JSONResponse(content={
                "update_available": True,
                "current_version": current_version,
                "latest_version": "0.2.3-beta",
                "download_url": "https://github.com/otiemann/py-aufsichtsplan/releases/download/v0.2.3-beta/Aufsichtsplan.exe",
                "release_notes": "🎉 Update-Test erfolgreich!\n\n✅ Update-Erkennung funktioniert\n✅ GitHub API Integration aktiv\n✅ Automatische Versionsprüfung\n\nDies ist ein Demo-Update um die Funktionalität zu zeigen.",
                "published_at": datetime.now().isoformat(),
                "size_mb": 87.5,
                "demo_mode": True,
                "auto_install_available": is_packaged_app
            })

        # Echter Update-Check (wird verwendet wenn Repository öffentlich ist)
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        if project_root not in sys.path:
            sys.path.insert(0, project_root)
        
        from updater import AutoUpdater
        
        updater = AutoUpdater(current_version=current_version)
        update_info = updater.check_for_updates()
        
        if update_info:
            return JSONResponse(content={
                "update_available": True,
                "current_version": current_version,
                "latest_version": update_info["version"],
                "download_url": update_info["download_url"],
                "release_notes": update_info["release_notes"],
                "published_at": update_info["published_at"],
                "size_mb": round(update_info["size"] / 1024 / 1024, 1) if update_info["size"] else 0,
                "auto_install_available": is_packaged_app
            })
        else:
            return JSONResponse(content={
                "update_available": False,
                "current_version": current_version,
                "message": "Sie verwenden bereits die neueste Version",
                "auto_install_available": is_packaged_app
            })

    except Exception as e:
        return JSONResponse(content={
            "error": True,
            "message": f"Fehler beim Prüfen auf Updates: {str(e)}"
        }, status_code=500)


@app.post("/api/install-update")
async def install_update(background_tasks: BackgroundTasks):
    """Lädt das aktuelle Release herunter und startet die Installation"""
    try:
        if not getattr(sys, "frozen", False) or os.name != "nt":
            return JSONResponse(content={
                "success": False,
                "message": "Automatische Updates stehen nur in der Windows-Installation zur Verfügung."
            }, status_code=400)

        current_version_info = get_version_info()
        current_version = current_version_info.get("version", "0.0.0")

        from updater import AutoUpdater

        updater = AutoUpdater(current_version=current_version)
        update_info = await run_in_threadpool(updater.check_for_updates)

        if not update_info:
            return JSONResponse(content={
                "success": False,
                "message": "Kein Update verfügbar."
            }, status_code=404)

        download_url = update_info.get("download_url")
        if not download_url:
            return JSONResponse(content={
                "success": False,
                "message": "Das Release enthält keine installierbare Datei."
            }, status_code=400)

        new_exe_path = await run_in_threadpool(lambda: updater.download_update(download_url))
        if not new_exe_path:
            return JSONResponse(content={
                "success": False,
                "message": "Das Update konnte nicht heruntergeladen werden."
            }, status_code=500)

        install_success = await run_in_threadpool(lambda: updater.install_update(new_exe_path))
        if not install_success:
            return JSONResponse(content={
                "success": False,
                "message": "Die Installation des Updates ist fehlgeschlagen."
            }, status_code=500)

        def delayed_shutdown() -> None:
            time.sleep(2)
            os.kill(os.getpid(), signal.SIGTERM)

        background_tasks.add_task(delayed_shutdown)

        return JSONResponse(content={
            "success": True,
            "message": "Update wurde installiert. Die Anwendung wird beendet und neu gestartet.",
            "latest_version": update_info.get("version")
        })

    except Exception as e:
        return JSONResponse(content={
            "success": False,
            "message": f"Fehler bei der Installation: {str(e)}"
        }, status_code=500)


@app.post("/shutdown")
async def shutdown():
    """Beendet die Anwendung ordentlich"""
    def stop_server():
        # Warte kurz, damit die Response noch gesendet werden kann
        import time
        time.sleep(1)
        # Sende SIGTERM an den aktuellen Prozess
        os.kill(os.getpid(), signal.SIGTERM)
    
    # Starte den Shutdown in einem separaten Thread
    shutdown_thread = threading.Thread(target=stop_server)
    shutdown_thread.daemon = True
    shutdown_thread.start()
    
    return JSONResponse(content={"message": "Server wird beendet..."}, status_code=200)


# Router erst NACH Initialisierung der Templates importieren,
# damit Router die zentrale Template-Engine aus main verwenden können
from .routers import admin as admin_router  # noqa: E402
from .routers import plan as plan_router    # noqa: E402

app.include_router(admin_router.router, prefix="/admin", tags=["admin"])
app.include_router(plan_router.router, prefix="/plan", tags=["plan"])
