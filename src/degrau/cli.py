"""Command line interface.

Each stage of the project adds a subcommand here. The CLI is thin on purpose: it
reads arguments, calls a domain function and prints. All the testable logic lives
in the modules, not in this file.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from degrau import __version__, config, library
from degrau.lexicon import Band, CoverageReport, LexiconError, coverage
from degrau.library import ImportAction, ImportResult
from degrau.profile import DEFAULT_NEW_WORDS, ProfileError, render
from degrau.store import database, texts

app = typer.Typer(
    help="degrau -- a graded English reader with its own flashcards.",
    no_args_is_help=True,
)

console = Console()
error_console = Console(stderr=True)


@app.command()
def version() -> None:
    """Show the project version."""
    console.print(f"degrau {__version__}")


def _print_report(report: CoverageReport, *, source: str, limit: int) -> None:
    percent = report.coverage_pct * 100
    verdict = "[green]within level[/green]" if report.meets_threshold else "[red]out of level[/red]"
    console.print(f"text: [cyan]{source}[/cyan]  target: [bold]{report.level.value}[/bold]")
    # Plain ASCII output on purpose: the Windows console mangles anything
    # outside its codepage and you lose the report.
    console.print(
        f"  {percent:.1f}% within {report.level.value}  "
        f"(threshold {report.threshold * 100:.0f}%)  {verdict}"
    )
    console.print(
        f"  {report.counted_tokens} counted tokens | "
        f"{report.out_of_level_tokens} above level | "
        f"{len(report.out_of_level)} distinct | "
        f"{report.na_tokens} names/numbers ignored\n"
    )

    if not report.out_of_level:
        console.print("[green]nothing above the target band[/green]")
        return

    shown = report.out_of_level[:limit]
    table = Table(title=f"Above {report.level.value} ({len(report.out_of_level)} lemmas)")
    table.add_column("lemma", style="cyan")
    table.add_column("as written")
    table.add_column("band")
    table.add_column("n", justify="right")
    table.add_column("zipf", justify="right")
    candidates = {word.lemma for word in report.candidates}
    for word in shown:
        table.add_row(
            word.lemma + ("" if word.lemma in candidates else " (known)"),
            word.display,
            word.band.value,
            str(word.count),
            "-" if word.zipf is None else f"{word.zipf:.1f}",
        )
    console.print(table)
    if len(report.out_of_level) > limit:
        console.print(f"  ... and {len(report.out_of_level) - limit} more (use --limit)")
    console.print(f"  [yellow]{len(report.candidates)}[/yellow] flashcard candidate(s)\n")


@app.command()
def analyze(
    text_file: Annotated[
        Path,
        typer.Argument(
            metavar="TEXT_FILE",
            help="Plain text file to measure. Use '-' to read stdin.",
        ),
    ],
    level: Annotated[Band, typer.Option("--level", "-l", help="Target CEFR level.")] = Band.A1,
    known: Annotated[
        Path | None,
        typer.Option("--known", help="File with one already-known lemma per line."),
    ] = None,
    limit: Annotated[
        int, typer.Option("--limit", help="How many above-level lemmas to list.")
    ] = 25,
) -> None:
    """Measure a text's coverage against a level and list what sits above it.

    Exits with code 1 when the text misses the threshold for its level -- that is
    what lets the /adapt loop know it has to try again.
    """
    try:
        if str(text_file) == "-":
            text, source = sys.stdin.read(), "<stdin>"
        else:
            text, source = text_file.read_text(encoding="utf-8"), str(text_file)
    except FileNotFoundError:
        error_console.print(f"[red]error:[/red] file not found: {text_file}")
        raise typer.Exit(code=1) from None
    except UnicodeDecodeError as error:
        error_console.print(f"[red]error:[/red] {text_file} is not UTF-8 text: {error}")
        raise typer.Exit(code=1) from error

    if not text.strip():
        error_console.print(f"[red]error:[/red] {source} is empty")
        raise typer.Exit(code=1)

    known_lemmas: list[str] = []
    if known is not None:
        try:
            known_lemmas = [line.strip() for line in known.read_text(encoding="utf-8").splitlines()]
        except FileNotFoundError:
            error_console.print(f"[red]error:[/red] known-words file not found: {known}")
            raise typer.Exit(code=1) from None

    try:
        report = coverage(text, level, [lemma for lemma in known_lemmas if lemma])
    except LexiconError as error:
        error_console.print(f"[red]error:[/red] {error}")
        raise typer.Exit(code=1) from error

    _print_report(report, source=source, limit=limit)

    if not report.meets_threshold:
        error_console.print(
            f"[red]below threshold[/red] for {report.level.value}: "
            f"{report.coverage_pct * 100:.1f}% < {report.threshold * 100:.0f}%"
        )
        sys.exit(1)


def _coverage_label(pct: float | None) -> str:
    return "-" if pct is None else f"{pct * 100:.1f}%"


def _print_import(result: ImportResult) -> None:
    colours = {
        ImportAction.IMPORTED: "green",
        ImportAction.DUPLICATE: "dim",
        ImportAction.REJECTED: "red",
    }
    colour = colours[result.action]
    line = f"[{colour}]{result.action.value:>9}[/{colour}]  {result.source}"
    if result.action is ImportAction.IMPORTED:
        flag = "" if result.meets_threshold else "  [yellow]below threshold[/yellow]"
        line += (
            f"  -> text {result.text_id}  {result.level}  "
            f"{_coverage_label(result.coverage_pct)} covered  "
            f"{result.out_of_level_count} above level{flag}"
        )
    elif result.reason:
        line += f"  ({result.reason})"
    console.print(line)


@app.command("import")
def import_inbox(
    file: Annotated[
        Path | None,
        typer.Argument(help="One file to import. Omit to drain the whole inbox."),
    ] = None,
) -> None:
    """Validate, measure and store the adapted texts waiting in the inbox.

    A file that does not match the contract is moved to inbox/rejected/ with a
    .error.txt beside it saying why -- never deleted. Importing the same text
    twice is a no-op, so re-running this is always safe.

    Exits with code 1 if anything was rejected, which is what lets /adapt know.
    """
    results = [library.import_file(file)] if file is not None else library.import_inbox()

    if not results:
        console.print(
            f"[yellow]nothing waiting[/yellow] in {config.inbox_dir()} -- "
            "run /adapt in Claude Code first."
        )
        return

    for result in results:
        _print_import(result)

    rejected = [result for result in results if result.action is ImportAction.REJECTED]
    imported = [result for result in results if result.action is ImportAction.IMPORTED]
    console.print(
        f"\n{len(imported)} imported, "
        f"{len(results) - len(imported) - len(rejected)} duplicate, "
        f"{len(rejected)} rejected"
    )
    if rejected:
        error_console.print(
            f"[red]{len(rejected)} file(s) rejected[/red] in {config.rejected_dir()}"
        )
        sys.exit(1)


@app.command()
def profile(
    level: Annotated[
        Band | None,
        typer.Option("--level", "-l", help="Target level. Defaults to the stored one."),
    ] = None,
    new_words: Annotated[
        int, typer.Option("--new-words", help="How many words to teach (5 to 8).")
    ] = DEFAULT_NEW_WORDS,
) -> None:
    """Print the context block to paste into an adaptation prompt.

    This is what /adapt runs first. It carries your vocabulary, not the level's
    word list: the model already knows which English words are frequent, and
    does not know which ones you have earned.
    """
    try:
        built = library.build_profile(level, new_words=new_words)
    except (ProfileError, LexiconError) as error:
        error_console.print(f"[red]error:[/red] {error}")
        raise typer.Exit(code=1) from error
    # print, not console.print: this output is piped into a prompt, and rich
    # would wrap it to the terminal width and add markup nobody asked for.
    sys.stdout.write(render(built) + "\n")


@app.command("texts")
def list_texts(
    limit: Annotated[int, typer.Option("--limit", help="How many to show.")] = 20,
) -> None:
    """List the imported texts, most recent first."""
    with database.session() as active:
        rows = texts.recent(active, limit=limit)
        table = Table(title=f"Library ({texts.count(active)} texts)")
        table.add_column("id", justify="right", style="cyan")
        table.add_column("level")
        table.add_column("title")
        table.add_column("covered", justify="right")
        table.add_column("imported")
        for row in rows:
            table.add_row(
                str(row.id),
                row.level,
                row.title or "(untitled)",
                _coverage_label(row.coverage_pct),
                # Stored in UTC, read by a person sitting in one timezone.
                row.created_at.astimezone().strftime("%Y-%m-%d %H:%M"),
            )

    if not rows:
        console.print("[yellow]library empty[/yellow] -- use 'degrau import'.")
        return
    console.print(table)


@app.command()
def serve(
    host: Annotated[str, typer.Option("--host", help="Interface to bind.")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", help="Port to listen on.")] = 8000,
    reload: Annotated[bool, typer.Option("--reload", help="Restart on code changes.")] = False,
) -> None:
    """Start the reading interface.

    Binds to localhost by default and should stay there. There is no
    authentication in this app by design, so anything that can reach the port
    can read and change the deck.
    """
    try:
        import uvicorn
    except ImportError:
        error_console.print("[red]error:[/red] web extras missing. Run: uv pip install -e '.[web]'")
        raise typer.Exit(code=1) from None

    if host not in {"127.0.0.1", "localhost", "::1"}:
        error_console.print(
            f"[yellow]warning:[/yellow] binding to {host}, which is not localhost. "
            "This app has no authentication."
        )

    console.print(f"reading at [cyan]http://{host}:{port}[/cyan]  (ctrl-c to stop)")
    uvicorn.run("degrau.web.app:app", host=host, port=port, reload=reload, log_level="warning")


if __name__ == "__main__":
    app()
