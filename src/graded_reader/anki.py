"""Writing the deck out as a file Anki can read.

Section 12 of the MVP says this stage is done when the file imports without
manual adjustment, so the details below are the whole job -- a CSV that needs
three clicks of fixing in the import dialog has failed even though it parses.

Three things earn their place, and each comes from Anki's own documentation on
text imports rather than from guessing:

**The escaping happens before the markup, never after.** With ``#html:true``
Anki reads every field as HTML, so an ampersand in a translation has to become
``&amp;`` -- but the ``<br>`` and ``<i>`` this module adds must stay literal.
Escape the content first, then wrap it. Reversed, the formatting is escaped into
visible angle brackets and every card is broken.

**``#tags column:3`` is what makes the third column tags.** Without it the
column is imported as a third *field*, which on a two-field note type silently
goes nowhere. The key really does contain a space.

**``#deck`` and ``#notetype`` preset, they do not create.** The manual is
explicit: "if it exists". Naming a deck that is not there is harmless and does
nothing, which is why the deck name is worth emitting and a note type name --
"Basic" is only called that in an English install -- is left to the caller.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Sequence

from pydantic import BaseModel, ConfigDict

from graded_reader.deck import SavedWord

#: The tag every note carries, so the whole import can be found or undone in
#: Anki with one search. The kind rides beside it, because word cards and
#: sentence cards want different review settings and a tag is how Anki filters.
SOURCE_TAG = "graded-reader"

#: Anki reads the first field to decide whether a note is a duplicate. Ours is
#: the word, so re-exporting updates the existing notes instead of piling up a
#: second copy of the deck.
DEDUPE_FIELD = "term"

_HTML_ESCAPES = (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"))


class Note(BaseModel):
    """One row of the file: front, back, tags."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    term: str
    back: str
    tags: str

    @property
    def usable(self) -> bool:
        """Whether the back has anything on it at all.

        A word saved by clicking it in a text arrives with only a band, so its
        card is blank on the reverse. It still exports -- hiding rows would be
        deciding for the reader -- but the count is worth reporting.
        """
        return bool(self.back.strip())


def escape_html(text: str) -> str:
    """The three replacements Anki's manual asks for, in the only safe order.

    The ampersand goes first. Doing it after the angle brackets would turn the
    ``&`` of a freshly written ``&lt;`` into ``&amp;lt;`` and print the entity
    instead of the character.
    """
    for raw, entity in _HTML_ESCAPES:
        text = text.replace(raw, entity)
    return text


def to_field(text: str) -> str:
    """Escape a value and turn its line breaks into the HTML kind."""
    escaped = escape_html(text.strip())
    return escaped.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>")


def tags_for(word: SavedWord, extra: Sequence[str] = ()) -> str:
    """The tags for one note, space separated as Anki expects.

    Spaces separate tags, so a tag cannot contain one. Anything that arrives
    with a space in it is joined up rather than silently becoming two tags.
    """
    parts = [SOURCE_TAG, word.kind]
    if word.band:
        parts.append(word.band)
    parts.extend(extra)
    return " ".join(part.strip().replace(" ", "-") for part in parts if part.strip())


def build_note(word: SavedWord, *, extra_tags: Sequence[str] = ()) -> Note:
    """One card as Anki will see it.

    A word card puts the word on the front, and on the back its translation
    followed by the example in italics -- the shape section 12 asks for. A
    sentence card puts the sentence on the front and what it means on the back,
    because on that kind the sentence *is* the card. Exporting one with the word
    on the front would hand Anki a different card from the one being studied
    here, and the schedule would stop matching what it schedules.
    """
    if word.is_sentence:
        return Note(
            term=to_field(word.sentence or word.display),
            back=to_field(word.sentence_pt),
            tags=tags_for(word, extra_tags),
        )

    translation = to_field(word.pt)
    example = to_field(word.example_en)

    if translation and example:
        back = f"{translation}<br><i>{example}</i>"
    elif example:
        back = f"<i>{example}</i>"
    else:
        back = translation

    return Note(
        term=to_field(word.display or word.lemma),
        back=back,
        tags=tags_for(word, extra_tags),
    )


def build_notes(words: Iterable[SavedWord], *, extra_tags: Sequence[str] = ()) -> list[Note]:
    return [build_note(word, extra_tags=extra_tags) for word in words]


def headers(*, deck: str = "", notetype: str = "") -> list[str]:
    """The ``#key:value`` lines that go above the rows.

    Only what is needed and known to be safe. ``notetype`` is not emitted by
    default: it presets nothing unless the name matches exactly, and the default
    note type is called something different in every localisation of Anki.
    """
    lines = ["#separator:Comma", "#html:true", "#columns:Term,Back,Tags", "#tags column:3"]
    if deck.strip():
        lines.append(f"#deck:{deck.strip()}")
    if notetype.strip():
        lines.append(f"#notetype:{notetype.strip()}")
    return lines


def render(
    notes: Sequence[Note],
    *,
    deck: str = "Graded Reader",
    notetype: str = "",
) -> str:
    """The whole file, ready to be written as UTF-8.

    The rows go through ``csv`` rather than being joined with commas: a
    translation containing a comma or a quote has to be quoted and doubled the
    way the format says, and hand-rolling that is how an export corrupts the one
    card whose translation had a comma in it.
    """
    buffer = io.StringIO()
    for line in headers(deck=deck, notetype=notetype):
        buffer.write(line + "\n")

    writer = csv.writer(buffer, lineterminator="\n")
    for note in notes:
        writer.writerow([note.term, note.back, note.tags])
    return buffer.getvalue()
