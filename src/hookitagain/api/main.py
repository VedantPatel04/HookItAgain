from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.middleware.cors import CORSMiddleware

from hookitagain.api.errors import error_response
from hookitagain.api.routes import api_router
from hookitagain.settings import get_settings


app = FastAPI(title = "hookitagain API")
settings = get_settings()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[str(settings.web_origin).rstrip("/")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(HTTPException)
async def handle_http_exception(request: Request, exc: HTTPException): #handles invalid auth errors
    if exc.status_code == 401:
        return error_response(
            401,
            "unauthorized",
            "Missing or invalid JWT.",
        )
    return await http_exception_handler(request, exc)


@app.get("/v1/health")
def health_check():
    return {"status": "ok"}


app.include_router(api_router, prefix="/v1")

