from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from starlette.middleware.sessions import SessionMiddleware
from app.core.config import settings
from app.api.v1 import auth, artist_applications, admin
from app.admin_panel.auth import AdminAuthRequired
from app.admin_panel import login_routes
from app.admin_panel import application_routes
from app.admin_panel import artwork_routes

app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_PREFIX}/openapi.json",
    docs_url=f"{settings.API_V1_PREFIX}/docs",
)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SECRET_KEY,
    session_cookie="admin_session",
    path="/admin",
    https_only=settings.cookie_secure,
)


@app.exception_handler(AdminAuthRequired)
async def admin_auth_required_handler(request, exc):
    return RedirectResponse(url="/admin/login", status_code=303)


app.include_router(auth.router, prefix=settings.API_V1_PREFIX + "/auth", tags=["Auth"])
app.include_router(
    artist_applications.router, prefix=settings.API_V1_PREFIX, tags=["Artist Application"]
)
app.include_router(admin.router, prefix=settings.API_V1_PREFIX, tags=["Admin"])
app.include_router(login_routes.router, prefix="/admin", tags=["Admin Panel"], include_in_schema=False)
app.include_router(application_routes.router, prefix="/admin", tags=["Admin Panel"], include_in_schema=False)
app.include_router(artwork_routes.router, prefix="/admin", tags=["Admin Panel"], include_in_schema=False)

@app.get("/health", tags=["Health"])
async def health_check():
    return {
        "status": "healthy",
        "environment": settings.ENVIRONMENT,
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)