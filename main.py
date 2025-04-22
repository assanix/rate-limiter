import logging
from fastapi import FastAPI, Request, Depends, Response

from rate_limiter import rate_limit, setup_rate_limiter, shutdown_rate_limiter
from config import settings

logging.basicConfig(level=logging.INFO)
app = FastAPI(title="Rate Limited API")

app.add_event_handler("startup", setup_rate_limiter)
app.add_event_handler("shutdown", shutdown_rate_limiter)


@app.middleware("http")
async def rate_limit_headers_middleware(request: Request, call_next):
    """Add rate limit headers to responses."""
    response = await call_next(request)

    if settings.RATE_LIMIT_RESPONSE_HEADERS_ENABLED:
        if hasattr(request.state, "rate_limit_headers"):
            for name, value in request.state.rate_limit_headers.items():
                response.headers[name] = value

    return response


@app.get("/")
async def root():
    """Unprotected endpoint."""
    return {"message": "Hello World"}


@app.get("/api/protected", dependencies=[Depends(rate_limit)])
async def protected():
    """Rate limited endpoint."""
    return {"message": "This endpoint is rate limited"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)