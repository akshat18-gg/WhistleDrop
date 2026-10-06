from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException


class ApiError(HTTPException):
    """An error we raise on purpose, with a code clients can check for."""

    def __init__(self, status_code: int, code: str, message: str, details=None, headers=None):
        super().__init__(status_code, detail=message, headers=headers)
        self.code = code
        self.details = details


def error_response(status_code: int, code: str, message: str, details=None, headers=None):
    body = {"error": {"code": code, "message": message, "details": details}}
    return JSONResponse(body, status_code=status_code, headers=headers)


async def handle_api_error(request: Request, exc: ApiError):
    return error_response(exc.status_code, exc.code, exc.detail, exc.details, exc.headers)


def register(app: FastAPI):
    app.add_exception_handler(ApiError, handle_api_error)
