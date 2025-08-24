import os
import signal
import threading
import shutil
from datetime import datetime
from fastapi import FastAPI, Request, HTTPException, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import JSONResponse, FileResponse
from sqlalchemy import text

from .database import Base, engine, SessionLocal, SQLALCHEMY_DATABASE_URL

app = FastAPI(title="Pausenaufsichtsplan")

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


@app.get("/admin/database")
async def database_admin(request: Request):
    """Zeigt die Datenbank-Verwaltungsseite"""
    try:
        db_path = get_db_file_path()
        db_exists = os.path.exists(db_path)
        db_size = os.path.getsize(db_path) if db_exists else 0
        db_modified = datetime.fromtimestamp(os.path.getmtime(db_path)) if db_exists else None
        
        return templates.TemplateResponse("admin/database.html", {
            "request": request,
            "db_path": db_path,
            "db_exists": db_exists,
            "db_size": f"{db_size / 1024:.1f} KB" if db_size > 0 else "0 KB",
            "db_modified": db_modified.strftime("%d.%m.%Y %H:%M:%S") if db_modified else "Unbekannt"
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Fehler beim Laden der Datenbank-Info: {str(e)}")


def get_db_file_path() -> str:
    """Ermittelt den tatsächlichen Pfad zur SQLite-Datenbank"""
    db_url = SQLALCHEMY_DATABASE_URL
    if db_url.startswith("sqlite:///"):
        return db_url[10:]  # Entferne "sqlite:///"
    raise ValueError("Nur SQLite-Datenbanken werden für Backup/Restore unterstützt")


@app.get("/backup/download")
async def download_backup():
    """Lädt die aktuelle Datenbank als Backup-Datei herunter"""
    try:
        db_path = get_db_file_path()
        
        if not os.path.exists(db_path):
            raise HTTPException(status_code=404, detail="Datenbank-Datei nicht gefunden")
        
        # Erstelle Backup-Dateinamen mit Zeitstempel
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_filename = f"vertretungsplan_backup_{timestamp}.db"
        
        return FileResponse(
            path=db_path,
            filename=backup_filename,
            media_type="application/octet-stream"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Backup-Fehler: {str(e)}")


@app.post("/backup/restore")
async def restore_backup(file: UploadFile = File(...)):
    """Stellt die Datenbank aus einer Backup-Datei wieder her"""
    try:
        # Validiere Dateiname und -typ
        if not file.filename or not file.filename.endswith('.db'):
            raise HTTPException(status_code=400, detail="Nur .db-Dateien sind erlaubt")
        
        db_path = get_db_file_path()
        backup_path = db_path + ".backup"
        
        # Erstelle Backup der aktuellen DB
        if os.path.exists(db_path):
            shutil.copy2(db_path, backup_path)
        
        try:
            # Schreibe die hochgeladene Datei
            content = await file.read()
            with open(db_path, "wb") as f:
                f.write(content)
            
            # Teste die neue Datenbank durch eine einfache Abfrage
            with SessionLocal() as db:
                db.execute(text("SELECT 1"))
            
            # Entferne das Backup, da alles erfolgreich war
            if os.path.exists(backup_path):
                os.remove(backup_path)
                
            return JSONResponse(content={
                "message": "Datenbank erfolgreich wiederhergestellt",
                "filename": file.filename
            })
            
        except Exception as e:
            # Stelle die ursprüngliche Datenbank wieder her
            if os.path.exists(backup_path):
                shutil.move(backup_path, db_path)
            raise HTTPException(status_code=400, detail=f"Ungültige Datenbank-Datei: {str(e)}")
            
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Restore-Fehler: {str(e)}")



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
                "demo_mode": True
            })
        
        # Echter Update-Check (wird verwendet wenn Repository öffentlich ist)
        import sys
        import os
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
                "size_mb": round(update_info["size"] / 1024 / 1024, 1) if update_info["size"] else 0
            })
        else:
            return JSONResponse(content={
                "update_available": False,
                "current_version": current_version,
                "message": "Sie verwenden bereits die neueste Version"
            })
            
    except Exception as e:
        return JSONResponse(content={
            "error": True,
            "message": f"Fehler beim Prüfen auf Updates: {str(e)}"
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
