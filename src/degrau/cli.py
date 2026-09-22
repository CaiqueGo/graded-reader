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

from degrau import __version__
from degrau.lexicon import Band, CoverageReport, LexiconError, coverage

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


if __name__ == "__main__":
    app()
