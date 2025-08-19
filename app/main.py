import os
import signal
import threading
from fastapi import FastAPI, Request, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import JSONResponse

from .database import Base, engine

app = FastAPI(title="Vertretungsplan / Pausenaufsicht")

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
