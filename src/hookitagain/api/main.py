from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.middleware.cors import CORSMiddleware

from hookitagain.api.errors import error_response
from hookitagain.api.routes import api_router
from hookitagain.settings import get_settings


app = FastAPI(title = "hookitagain API")
settings = get_settings()

# allows frontend and backend to communicate from different ports, domains,
# protocols (http, https, etc.)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[str(settings.web_origin).rstrip("/")],
    allow_credentials=True,
    allow_methods=["*"], #allow specific http method requests from different origins (PUT, POST or
                         #use * for wildcard "all of them")
    allow_headers=["*"], #allow specific headers from different origins (Content-Type, Authorization, etc.)
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


@app.get("/api/health")
def health_check():
    return {"status": "ok"}


app.include_router(api_router, prefix="/api")

