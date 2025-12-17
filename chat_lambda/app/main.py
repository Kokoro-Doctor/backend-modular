from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from mangum import Mangum
from app.routers import chat_router
from app.logger import get_logger

logger = get_logger(__name__)

app = FastAPI(title="Kokoro Chat Service")

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://kokoro.doctor", "https://metafied.co", "http://localhost:8081"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

# Custom HTTPException handler to preserve CORS headers
@app.exception_handler(HTTPException)
async def custom_http_exception_handler(request: Request, exc: HTTPException):
    # Choose log level based on error severity
    if 400 <= exc.status_code < 500:
        logger.warning(f"[HTTP {exc.status_code}] {exc.detail}")
    else:  # 500 and above
        logger.error(f"[HTTP {exc.status_code}] {exc.detail}")

    # Get the origin from the request and validate it against allowed origins
    origin = request.headers.get("origin")
    allowed_origins = ["https://kokoro.doctor", "https://metafied.co", "http://localhost:8081"]
    
    # Set CORS headers - only include origin if it's in the allowed list
    headers = {}
    if origin in allowed_origins:
        headers["Access-Control-Allow-Origin"] = origin
        headers["Access-Control-Allow-Credentials"] = "true"

    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers=headers
    )
app.include_router(chat_router.router)

handler = Mangum(app)
