import os
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request

from .database import Base, engine

# Router
from .routers import admin as admin_router
from .routers import plan as plan_router

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


# Include routers
app.include_router(admin_router.router, prefix="/admin", tags=["admin"])
app.include_router(plan_router.router, prefix="/plan", tags=["plan"])
