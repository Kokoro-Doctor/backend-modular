from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from mangum import Mangum

from app.routers import abha_router, hip_linking_router, webhook_router, hiu_router
from app.logger import get_logger, set_log_source

logger = get_logger(__name__)

app = FastAPI(title="ABHA Service")


@app.middleware("http")
async def tag_log_source(request: Request, call_next):
    """
    Tag every log line for this request with its origin so ABDM webhook logs
    and our own trigger logs can be separated in CloudWatch.

    Our own endpoints live under /abha (abha, hip-linking, hiu routers); the
    inbound ABDM callbacks live under /api/v3 (webhook router, paths fixed by
    the ABDM spec). Only /api/v3 paths are tagged as ABDM webhooks — everything
    else (our /abha endpoints, /docs, health checks) counts as internal.
    """
    path = request.url.path
    source = "abdm_webhook" if path.startswith("/api/v3") else "internal"
    set_log_source(source)
    return await call_next(request)

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
app.include_router(hiu_router.router)
app.include_router(webhook_router.router)

handler = Mangum(app)
