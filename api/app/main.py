from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config.settings import settings

app = FastAPI(title="FisioGreat Demo API", version="1.0.0")

# Evita normalizaciones automáticas
app.router.redirect_slashes = False

# CORS
origins = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Santé: alias robusto (usa este para monitors/Cloud Run)
@app.get("/__health", include_in_schema=False)
@app.get("/__health/", include_in_schema=False)
def __health():
    return {"ok": True, "env": settings.APP_ENV}

# Mantén también /healthz por compatibilidad
@app.get("/healthz", include_in_schema=False)
@app.get("/healthz/", include_in_schema=False)
def healthz():
    return {"ok": True, "env": settings.APP_ENV}

# (Opcional) que "/" también responda
@app.get("/", include_in_schema=False)
def root():
    return {"status": "ok", "env": settings.APP_ENV}

# Importar routers
from .routers.whatsapp import router as whatsapp_router
from .routers.voice import router as voice_router

app.include_router(whatsapp_router)
app.include_router(voice_router)
