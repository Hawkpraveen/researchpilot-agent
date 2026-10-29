import logging

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.config.settings import get_settings
from app.middleware.request_id import RequestIdMiddleware
from app.routes.health import router as health_router
from app.routes.research import router as research_router

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)

    application = FastAPI(
        title="ResearchPilot Agent",
        version="0.1.0",
    )

    application.add_middleware(RequestIdMiddleware)
    application.include_router(health_router)
    application.include_router(research_router, prefix="/api/v1")

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request,
        error: RequestValidationError,
    ) -> JSONResponse:
        request_id = getattr(request.state, "request_id", "")
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "The request body is invalid.",
                    "retryable": False,
                    "requestId": request_id,
                    "details": [
                        {
                            "path": ".".join(str(part) for part in issue["loc"]),
                            "message": issue["msg"],
                        }
                        for issue in error.errors()
                    ],
                }
            },
        )

    @application.exception_handler(Exception)
    async def unexpected_error_handler(
        request: Request,
        error: Exception,
    ) -> JSONResponse:
        request_id = getattr(request.state, "request_id", "")
        logger.exception(
            "Unhandled Agent request error",
            extra={"request_id": request_id},
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "The research agent could not complete the request.",
                    "retryable": True,
                    "requestId": request_id,
                },
                "usage": getattr(error, "usage", None),
            },
        )

    return application


app = create_app()


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
    )
