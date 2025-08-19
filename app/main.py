from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request

from .database import Base, engine

# Router
from .routers import admin as admin_router
from .routers import plan as plan_router

app = FastAPI(title="Vertretungsplan / Pausenaufsicht")

templates = Jinja2Templates(directory="app/templates")

# Static (optional)
# app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.on_event("startup")
def on_startup() -> None:
    Base.metadata.create_all(bind=engine)


@app.get("/")
async def root(request: Request):
    return templates.TemplateResponse("base.html", {"request": request, "content": "Willkommen"})


# Include routers
app.include_router(admin_router.router, prefix="/admin", tags=["admin"])
app.include_router(plan_router.router, prefix="/plan", tags=["plan"])
