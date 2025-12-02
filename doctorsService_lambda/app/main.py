from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from mangum import Mangum
from app.logger import logger
from app.routers import profile_router, fetch_router, subscribe_router, slots_router

app = FastAPI(title="Kokoro Doctor Service")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://kokoro.doctor", "http://localhost:8081", "http://metafied.co/"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.exception_handler(HTTPException)
async def custom_http_exception_handler(request: Request, exc: HTTPException):
    if 400 <= exc.status_code < 500:
        logger.warning(f"[HTTP {exc.status_code}] {exc.detail}")
    else:  # 500 and above
        logger.error(f"[HTTP {exc.status_code}] {exc.detail}")

    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers={
            "Access-Control-Allow-Origin": "https://kokoro.doctor, http://metafied.co/",
            "Access-Control-Allow-Credentials": "true",
        },
    )

# Routers
app.include_router(profile_router.router)
app.include_router(fetch_router.router)
app.include_router(subscribe_router.router)
app.include_router(slots_router.router)

handler = Mangum(app)
