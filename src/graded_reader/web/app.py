"""The web application.

Server-rendered, with HTMX for the parts that change without a page load. There
is no build step and no JavaScript of our own beyond a few small inline scripts:
keyboard bindings for review, the selection that saves a sentence, and the
browser's own speech synthesis reading a text aloud. The reading screen is text,
and the review screen needs a keyboard, not a framework.

Localhost, single user, no authentication -- which is a decision the MVP makes,
not an omission. It is also why this app must never be bound to a public
interface: there is nothing here that would stop anyone who reached it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from graded_reader import __version__, preparation
from graded_reader.web.assets import static_url
from graded_reader.web.routes import router

HERE = Path(__file__).parent
TEMPLATES_DIR = HERE / "templates"
STATIC_DIR = HERE / "static"

#: Autoescaping is on, which is what Jinja2Templates does by default for .html.
#: It matters more here than in most apps: the adapted text and its glossary
#: come from a language model, and are rendered back as markup.
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

#: Templates call this instead of writing /static/... by hand, so a changed file
#: reaches the browser without anyone having to clear a cache. See web.assets.
templates.env.globals["static_url"] = lambda name: static_url(name, root=STATIC_DIR)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Start writing today's text as soon as the server is up.

    It runs in a background thread and returns at once, so the server is not
    held up by a model call. If today is already prepared, the claim finds it
    and the thread ends without doing anything.
    """
    preparation.ensure_today()
    yield


def create_app() -> FastAPI:
    """Build the application.

    A factory rather than a module-level app so a test, or a second instance
    pointed at another database, does not have to import a singleton.
    """
    app = FastAPI(
        title="Graded Reader",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(router)
    return app


app = create_app()
