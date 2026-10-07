import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from starlette.datastructures import Headers

from app import errors, evidence
from app.db import create_tables
from app.errors import ApiError, error_response
from app.routers import auth, moderator, reports
from app.security import limiter

# No timestamp in the format on purpose: a timed log line for POST /api/reports
# would give away exactly when a report came in.
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("whistledrop")

MAX_BODY_BYTES = 32 * 1024
BODY_LIMITS = {"/api/reports/evidence": evidence.MAX_BYTES}

DESCRIPTION = """
A confidential reporting API. Anyone can submit a report without an account or a name,
and track it later with the case code they get back.

**Reporters:** submit with `POST /api/reports` and save the `case_code` from the response.
To check on it, call `GET /api/reports/status` with the code in the `X-Case-Code` header.

**Moderators:** log in with `POST /api/auth/login`, click **Authorize** and paste the `access_token`.

Every error looks like `{"error": {"code", "message", "details"}}`.
"""

TAGS = [
    {"name": "Reporters", "description": "Public. No account needed."},
    {"name": "Moderator login", "description": "Moderator accounts are made with `python -m app.cli create-moderator`."},
    {"name": "Moderators", "description": "Need `Authorization: Bearer <token>`."},
    {"name": "Health"},
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_tables()
    yield


app = FastAPI(
    title="WhistleDrop",
    version="1.0.0",
    description=DESCRIPTION,
    openapi_tags=TAGS,
    lifespan=lifespan,
)
app.state.limiter = limiter
errors.register(app)
app.include_router(reports.router)
app.include_router(auth.router)
app.include_router(moderator.router)


@app.get("/health", tags=["Health"], summary="Health check")
def health():
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def root():
    # There's no frontend, so the bare link opens Swagger instead of a 404.
    return RedirectResponse("/docs")


def openapi_schema():
    # FastAPI adds its own 422 entry, in its own error shape, to every route with
    # parameters. We document our 422s by hand where they can actually happen,
    # so drop the generated ones.
    if app.openapi_schema is None:
        schema = FastAPI.openapi(app)
        for operations in schema["paths"].values():
            for operation in operations.values():
                if operation["responses"].get("422", {}).get("description") == "Validation Error":
                    del operation["responses"]["422"]
        schema["components"]["schemas"].pop("HTTPValidationError", None)
        schema["components"]["schemas"].pop("ValidationError", None)
    return app.openapi_schema


app.openapi = openapi_schema


def readable_size(size: int) -> str:
    return f"{size // (1024 * 1024)} MB" if size >= 1024 * 1024 else f"{size // 1024} KB"


class LimitBodySize:
    """Refuses bodies over 32 KB (5 MB for evidence files), whether or not the client says the size up front."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        limit = BODY_LIMITS.get(scope["path"], MAX_BODY_BYTES)
        too_large = ApiError(413, "BODY_TOO_LARGE", f"The request body can't be bigger than {readable_size(limit)}.")
        declared = Headers(scope=scope).get("content-length", "")
        if declared.isdigit() and int(declared) > limit:
            response = error_response(too_large.status_code, too_large.code, too_large.detail)
            return await response(scope, receive, send)

        received = 0

        async def receive_with_limit():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
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
