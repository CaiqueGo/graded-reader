"""HTTP endpoints.

Thin on purpose, exactly like the CLI: read the request, call a domain function,
render. Every one of these should be readable in one screen, and none of them
should contain a rule you would want to test. When one starts to grow, the part
that grew belongs in ``reading`` or ``deck``.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session

from degrau import deck, reading, review
from degrau.reading import ReadingError
from degrau.review import ReviewError
from degrau.store import database, texts

router = APIRouter()


def get_session() -> Iterator[Session]:
    """One transaction per request, committed when the handler returns."""
    with database.session() as active:
        yield active


SessionDep = Annotated[Session, Depends(get_session)]


def render(request: Request, name: str, **context: object) -> HTMLResponse:
    """Render a template.

    Imported inside the function because app.py imports this module to build the
    router: at module level this would be a cycle.
    """
    from degrau.web.app import templates

    response: HTMLResponse = templates.TemplateResponse(request, name, context)
    return response


@router.get("/", response_class=HTMLResponse)
def library(request: Request, session: SessionDep) -> HTMLResponse:
    """The list of imported texts."""
    return render(request, "index.html", texts=reading.summaries(session), tab="reading")


@router.get("/texts/{text_id}", response_class=HTMLResponse)
def read_text(request: Request, text_id: int, session: SessionDep) -> HTMLResponse:
    """The reading screen for one text."""
    try:
        view = reading.build_view(session, text_id)
    except ReadingError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return render(request, "text.html", view=view, tab="reading")


@router.get("/texts/{text_id}/original", response_class=HTMLResponse)
def original(request: Request, text_id: int, session: SessionDep) -> HTMLResponse:
    """The untouched source, shown beside the adapted version."""
    row = texts.by_id(session, text_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"no text with id {text_id}")
    return render(
        request,
        "partials/original.html",
        paragraphs=reading.split_paragraphs(row.original_text),
        text_id=text_id,
    )


@router.get("/texts/{text_id}/adapted", response_class=HTMLResponse)
def adapted(request: Request, text_id: int, session: SessionDep) -> HTMLResponse:
    """Back to the adapted version after looking at the original."""
    try:
        view = reading.build_view(session, text_id)
    except ReadingError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return render(request, "partials/adapted.html", view=view)


@router.get("/card", response_class=HTMLResponse)
def word_card(
    request: Request,
    session: SessionDep,
    lemma: Annotated[str, Query(min_length=1, max_length=80)],
    text_id: Annotated[int | None, Query()] = None,
) -> HTMLResponse:
    """The side panel for a clicked word."""
    try:
        card = reading.build_card(session, lemma, text_id=text_id)
    except ReadingError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return render(request, "partials/card.html", card=card)


@router.post("/words", response_class=HTMLResponse)
def save_word(
    request: Request,
    session: SessionDep,
    lemma: Annotated[str, Form(min_length=1, max_length=80)],
    text_id: Annotated[int | None, Form()] = None,
    display: Annotated[str, Form(max_length=120)] = "",
    pt: Annotated[str, Form(max_length=500)] = "",
    example_en: Annotated[str, Form(max_length=1000)] = "",
    example_pt: Annotated[str, Form(max_length=1000)] = "",
) -> HTMLResponse:
    """Put a word in the deck and hand back the refreshed side panel."""
    try:
        deck.save_word(
            session,
            lemma,
            display=display,
            pt=pt or None,
            example_en=example_en or None,
            example_pt=example_pt or None,
            first_text_id=text_id,
        )
    except deck.DeckError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    card = reading.build_card(session, lemma, text_id=text_id)
    return render(request, "partials/card.html", card=card, just_saved=True)


@router.post("/texts/{text_id}/glossary", response_class=HTMLResponse)
def save_glossary(request: Request, text_id: int, session: SessionDep) -> HTMLResponse:
    """Save every glossary word of a text at once.

    Words already in the deck are left exactly as they are, schedule included --
    ``deck.save_word`` is what guarantees that, not this handler.
    """
    try:
        view = reading.build_view(session, text_id)
    except ReadingError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error

    for item in view.glossary:
        if item.lemma:
            deck.save_word(
                session,
                item.lemma,
                display=item.en,
                pt=item.pt or None,
                example_en=item.example_en or None,
                example_pt=item.example_pt or None,
                first_text_id=text_id,
            )

    return render(request, "partials/glossary.html", view=reading.build_view(session, text_id))


@router.get("/texts/{text_id}/answer/{index}", response_class=HTMLResponse)
def reveal_answer(request: Request, text_id: int, index: int, session: SessionDep) -> HTMLResponse:
    """Show the answer to one comprehension question."""
    try:
        view = reading.build_view(session, text_id)
    except ReadingError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error

    found = next((item for item in view.questions if item.index == index), None)
    if found is None:
        raise HTTPException(status_code=404, detail=f"no question {index}")
    return render(request, "partials/answer.html", question=found)


def _review_context(session: Session) -> dict[str, object]:
    """Card plus counters -- the two things every review response carries."""
    return {"card": review.next_card(session), "counts": review.counts(session)}


@router.get("/review", response_class=HTMLResponse)
def review_screen(request: Request, session: SessionDep) -> HTMLResponse:
    """The review session: one card at a time, driven by the keyboard."""
    return render(request, "review.html", tab="review", **_review_context(session))


@router.post("/review/grade", response_class=HTMLResponse)
def grade_card(
    request: Request,
    session: SessionDep,
    word_id: Annotated[int, Form()],
    rating: Annotated[int, Form(ge=1, le=4)],
) -> HTMLResponse:
    """Apply a rating and hand back the next card."""
    try:
        review.grade(session, word_id, rating)
    except ReviewError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return render(request, "partials/review_card.html", **_review_context(session))


@router.post("/review/undo", response_class=HTMLResponse)
def undo(request: Request, session: SessionDep) -> HTMLResponse:
    """Take back the last answer and show that card again."""
    try:
        undone = review.undo_last(session)
    except deck.DeckError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return render(request, "partials/review_card.html", undone=undone, **_review_context(session))
