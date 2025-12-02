from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from mangum import Mangum
from app.logger import logger
from app.routers import booking_router

app = FastAPI(title="Doctor Booking Service")

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://kokoro.doctor", "http://localhost:8081", "http://metafied.co/"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

# Custom exception handler
@app.exception_handler(HTTPException)
async def custom_http_exception_handler(request: Request, exc: HTTPException):
        # Choose log level based on error severity
    if 400 <= exc.status_code < 500:
        logger.warning(f"[HTTP {exc.status_code}] {exc.detail}")
    else:  # 500 and above
        logger.error(f"[HTTP {exc.status_code}] {exc.detail}")

    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers={
            "Access-Control-Allow-Origin": "https://kokoro.doctor, http://metafied.co/",
            "Access-Control-Allow-Credentials": "true"
        }
    )

# Routers
app.include_router(booking_router.router, prefix="/doctorBookings", tags=["Doctor Bookings"])

# AWS Lambda handler
handler = Mangum(app)
