from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from mangum import Mangum
from starlette.middleware.base import BaseHTTPMiddleware
from app.logger import get_logger
from app.routers import hospital_router, staff_router

logger = get_logger(__name__)
app = FastAPI(title="Kokoro Hospitals Service")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log every incoming request to help debug routing and auth issues."""

    async def dispatch(self, request, call_next):
        path = request.url.path
        method = request.method
        logger.info(f"[HOSPITALS] Incoming request: {method} {path}")
        response = await call_next(request)
        logger.info(f"[HOSPITALS] Response: {method} {path} -> {response.status_code}")
        return response


app.add_middleware(RequestLoggingMiddleware)
# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://kokoro.doctor", "http://localhost:8081"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Custom HTTPException handler to preserve CORS headers
@app.exception_handler(HTTPException)
async def custom_http_exception_handler(request: Request, exc: HTTPException):
    if 400 <= exc.status_code < 500:
        get_logger(__name__).warning(f"[HTTP {exc.status_code}] {exc.detail}")
    else:
        get_logger(__name__).error(f"[HTTP {exc.status_code}] {exc.detail}")

    origin = request.headers.get("origin")
    allowed_origins = ["https://kokoro.doctor", "http://localhost:8081"]
    headers = {}
    if origin in allowed_origins:
        headers["Access-Control-Allow-Origin"] = origin
        headers["Access-Control-Allow-Credentials"] = "true"

    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers=headers,
    )

# Routers
app.include_router(hospital_router.router)
app.include_router(staff_router.router)

handler = Mangum(app)
