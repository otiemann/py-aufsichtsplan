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

# Logging-Hinweis, falls etwas fehlt
if not os.path.isdir(TEMPLATES_DIR):
	print(f"[WARN] Templates-Verzeichnis nicht gefunden: {TEMPLATES_DIR}")
if not os.path.isdir(STATIC_DIR):
	print(f"[WARN] Static-Verzeichnis nicht gefunden: {STATIC_DIR}")

templates = Jinja2Templates(directory=TEMPLATES_DIR)

# Static
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.on_event("startup")
def on_startup() -> None:
    Base.metadata.create_all(bind=engine)


@app.get("/")
async def root(request: Request):
    return templates.TemplateResponse("base.html", {"request": request, "content": "Willkommen"})


# Include routers
app.include_router(admin_router.router, prefix="/admin", tags=["admin"])
app.include_router(plan_router.router, prefix="/plan", tags=["plan"])
