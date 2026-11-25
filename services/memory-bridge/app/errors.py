"""Standard error types and JSON error responses without leaking private paths."""
from __future__ import annotations

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class BridgeError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def error_payload(code: str, message: str) -> dict[str, dict[str, str]]:
    return {"error": {"code": code, "message": message}}


async def bridge_error_handler(_request: Request, exc: BridgeError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=error_payload(exc.code, exc.message))


async def validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    details = []
    for err in exc.errors()[:5]:
        loc = ".".join(str(part) for part in err.get("loc", []))
        msg = err.get("msg", "Invalid input")
        details.append(f"{loc}: {msg}" if loc else msg)
    summary = "; ".join(details) if details else "Validation failed"
    return JSONResponse(status_code=422, content=error_payload("validation_error", summary))
