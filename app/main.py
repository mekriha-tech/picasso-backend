from fastapi import FastAPI
from app.core.config import settings
from app.api.v1 import auth, artist_applications, admin

app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_PREFIX}/openapi.json",
    docs_url=f"{settings.API_V1_PREFIX}/docs",
)

app.include_router(auth.router, prefix=settings.API_V1_PREFIX + "/auth", tags=["Auth"])
app.include_router(
    artist_applications.router, prefix=settings.API_V1_PREFIX, tags=["Artist Application"]
)
app.include_router(admin.router, prefix=settings.API_V1_PREFIX, tags=["Admin"])

@app.get("/health", tags=["Health"])
async def health_check():
    return {
        "status": "healthy",
        "environment": settings.ENVIRONMENT,
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)