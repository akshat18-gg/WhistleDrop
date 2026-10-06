import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from starlette.datastructures import Headers

from app import errors
from app.db import create_tables
from app.errors import ApiError, error_response
from app.routers import auth, moderator, reports

# No timestamp in the format on purpose: a timed log line for POST /api/reports
# would give away exactly when a report came in.
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("whistledrop")

MAX_BODY_BYTES = 32 * 1024


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_tables()
    yield


app = FastAPI(title="WhistleDrop", version="1.0.0", lifespan=lifespan)
errors.register(app)
app.include_router(reports.router)
app.include_router(auth.router)
app.include_router(moderator.router)


class LimitBodySize:
    """Refuses bodies over 32 KB, whether or not the client says the size up front."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        too_large = ApiError(413, "BODY_TOO_LARGE", f"The request body can't be bigger than {MAX_BODY_BYTES // 1024} KB.")
        declared = Headers(scope=scope).get("content-length", "")
        if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
            response = error_response(too_large.status_code, too_large.code, too_large.detail)
            return await response(scope, receive, send)

        received = 0

        async def receive_with_limit():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > MAX_BODY_BYTES:
                    raise too_large
            return message

        await self.app(scope, receive_with_limit, send)


app.add_middleware(LimitBodySize)


def route_template(request: Request) -> str:
    route = request.scope.get("route")
    # For unknown URLs we don't log the path at all. It could contain anything,
    # including a case code someone pasted into the address bar.
    return route.path if route else "(no matching route)"


@app.middleware("http")
async def headers_and_logging(request: Request, call_next):
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        # The traceback is logged, the request body never is: it may hold report text.
        logger.exception("Unhandled error in %s %s", request.method, route_template(request))
        response = error_response(500, "INTERNAL_ERROR", "Something went wrong on our side. Please try again later.")

    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"

    elapsed_ms = (time.perf_counter() - started) * 1000
    logger.info("%s %s %s %.0fms", request.method, route_template(request), response.status_code, elapsed_ms)
    return response


@app.get("/health")
def health():
    return {"status": "ok"}
