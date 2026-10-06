import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException

logger = logging.getLogger("whistledrop")


class ApiError(HTTPException):
    """An error we raise on purpose, with a code clients can check for."""

    def __init__(self, status_code: int, code: str, message: str, details=None, headers=None):
        super().__init__(status_code, detail=message, headers=headers)
        self.code = code
        self.details = details


class ErrorInfo(BaseModel):
    code: str
    message: str
    details: list[dict[str, str]] | None = None


class ErrorOut(BaseModel):
    error: ErrorInfo


def documented(errors: dict[int, str]) -> dict:
    """Describes the errors a route can return, for the OpenAPI docs."""
    return {status: {"model": ErrorOut, "description": text} for status, text in errors.items()}


def error_response(status_code: int, code: str, message: str, details=None, headers=None):
    body = {"error": {"code": code, "message": message, "details": details}}
    return JSONResponse(body, status_code=status_code, headers=headers)


# For errors raised by FastAPI or Starlette themselves, not by our code.
FRAMEWORK_ERRORS = {
    400: ("BAD_REQUEST", "The request couldn't be read."),
    404: ("NOT_FOUND", "There's nothing at this address."),
    405: ("METHOD_NOT_ALLOWED", "This address doesn't support that method."),
}

SIMPLE_PROBLEMS = {
    "missing": "This field is required.",
    "extra_forbidden": "This field isn't accepted here.",
    "string_type": "Must be text.",
    "bool_type": "Must be true or false.",
    "bool_parsing": "Must be true or false.",
    "int_type": "Must be a whole number.",
    "int_parsing": "Must be a whole number.",
    "uuid_type": "Must be a report id, like 3f2b8c1e-5d4a-4b7e-9c2f-1a2b3c4d5e6f.",
    "uuid_parsing": "Must be a report id, like 3f2b8c1e-5d4a-4b7e-9c2f-1a2b3c4d5e6f.",
    "model_attributes_type": "Must be a JSON object.",
    "dict_type": "Must be a JSON object.",
}


def describe_problem(error: dict) -> str:
    kind = error["type"]
    ctx = error.get("ctx", {})
    if kind in SIMPLE_PROBLEMS:
        return SIMPLE_PROBLEMS[kind]
    if kind == "string_too_short":
        if ctx["min_length"] == 1:
            return "Can't be empty."
        return f"Must be at least {ctx['min_length']} characters, not counting spaces at the ends."
    if kind == "string_too_long":
        return f"Must be at most {ctx['max_length']} characters."
    if kind in ("enum", "literal_error"):
        return f"Must be one of {ctx['expected']}."
    if kind.startswith("date"):
        return "Must be a date like 2026-10-04."
    if kind == "greater_than_equal":
        return f"Must be {ctx['ge']} or more."
    if kind == "less_than_equal":
        return f"Must be {ctx['le']} or less."
    if kind == "value_error":
        return str(ctx["error"])
    return error["msg"]


def field_name(location: tuple) -> str:
    # ("body", "description") -> "description", ("query", "from") -> "from"
    return ".".join(str(part) for part in location[1:]) or str(location[0])


async def handle_http_error(request: Request, exc: HTTPException):
    if isinstance(exc, ApiError):
        return error_response(exc.status_code, exc.code, exc.detail, exc.details, exc.headers)
    code, message = FRAMEWORK_ERRORS.get(exc.status_code, ("ERROR", str(exc.detail)))
    return error_response(exc.status_code, code, message, headers=exc.headers)


async def handle_validation_error(request: Request, exc: RequestValidationError):
    errors = exc.errors()
    # FastAPI only parses the body as JSON when the content type says so; any other
    # body shows up here as raw bytes. Either way the client didn't send JSON.
    if any(error["type"] == "json_invalid" for error in errors) or isinstance(exc.body, bytes):
        return error_response(400, "INVALID_JSON", "The request body has to be valid JSON.")
    details = [{"field": field_name(error["loc"]), "problem": describe_problem(error)} for error in errors]
    return error_response(422, "VALIDATION_ERROR", "Some fields aren't right. See details.", details)


def register(app: FastAPI):
    app.add_exception_handler(HTTPException, handle_http_error)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
