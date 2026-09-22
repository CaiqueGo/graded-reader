"""The web application.

Server-rendered, with HTMX for the parts that change without a page load. There
is no build step and no JavaScript of our own beyond a few keyboard bindings: the
reading screen is text, and the review screen that comes next needs a keyboard,
not a framework.

Localhost, single user, no authentication -- which is a decision the MVP makes,
not an omission. It is also why this app must never be bound to a public
interface: there is nothing here that would stop anyone who reached it.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from degrau import __version__
from degrau.web.routes import router

HERE = Path(__file__).parent
TEMPLATES_DIR = HERE / "templates"
STATIC_DIR = HERE / "static"

#: Autoescaping is on, which is what Jinja2Templates does by default for .html.
#: It matters more here than in most apps: the adapted text and its glossary
#: come from a language model, and are rendered back as markup.
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def create_app() -> FastAPI:
    """Build the application.

    A factory rather than a module-level app so a test, or a second instance
    pointed at another database, does not have to import a singleton.
    """
    app = FastAPI(
        title="Degrau",
        version=__version__,
        docs_url=None,
        redoc_url=None,
    )
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(router)
    return app


app = create_app()
