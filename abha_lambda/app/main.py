from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from mangum import Mangum

from app.routers import abha_router, hip_linking_router, webhook_router
from app.logger import get_logger

logger = get_logger(__name__)

app = FastAPI(title="ABHA Service")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://kokoro.doctor", "http://localhost:8081"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=[],
)


@app.exception_handler(HTTPException)
async def custom_http_exception_handler(request: Request, exc: HTTPException):
    if 400 <= exc.status_code < 500:
        logger.warning(f"[HTTP {exc.status_code}] {exc.detail}")
    else:
        logger.error(f"[HTTP {exc.status_code}] {exc.detail}")

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


app.include_router(abha_router.router)
app.include_router(hip_linking_router.router)
app.include_router(webhook_router.router)

handler = Mangum(app)
