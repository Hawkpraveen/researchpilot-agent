import re
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,80}$")


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        candidate = request.headers.get("x-request-id", "")
        request_id = candidate if _REQUEST_ID_PATTERN.fullmatch(candidate) else str(uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
