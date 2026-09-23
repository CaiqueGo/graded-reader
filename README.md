# Graded Reader

A graded English reader with its own flashcards and a progress dashboard.

Graded Reader takes **any** text and rewrites it at the right level, pulls out the
vocabulary worth learning, and tracks what actually stuck over time.

Every deterministic piece of work belongs to the app — validation, lemmatisation,
coverage measurement, scheduling, statistics. The rewriting belongs to Claude Code,
through the binary you already have installed, and you can trigger it from the web
or from the terminal.

## Where to start

- [`docs/how-it-works.md`](docs/how-it-works.md) — **how the system works today**:
  every screen, what the glossary is, how the review queue decides what to show,
  and what each number on the dashboard claims. Start here to use it.
- [`docs/graded-reader-mvp.md`](docs/graded-reader-mvp.md) — the full specification: the
  problem, the product thesis, the import contract, the data model and the milestones.
- [`docs/v2-ideas.md`](docs/v2-ideas.md) — what has been raised for v2, with the cost
  and the catch of each idea. **Nothing decided.**

## State

| Milestone | What it delivers | Status |
|---|---|---|
| M0 | Lexicon: bands, lemmatisation, coverage, `reader analyze` | done |
| M1 | The loop closed from the terminal: database, `profile`, importer | done |
| M2 | Reading on the web: read, click a word, save it to the deck | done |
| M3 | Review: the day's queue, keyboard, undo, daily limit | done |
| M4 | Dashboard: numbers, the level ladder, history, retention | done |
| M5 | Anki export | done |
| — | Adapting from the web (anticipates §14's `ApiAdapter`) | done |

## Running it

```
uv venv --python 3.11
uv pip install -e ".[lexicon,db,srs,web,adapt,dev]"
python -m spacy download en_core_web_sm
```

The normal path is `reader serve` and the **Add a text** button — paste an address or
the article, pick the level, done. The same loop from the terminal, when you want
control:

```
/adapt A1 article.txt
```

It runs `reader profile`, adapts the text, writes the JSON into `inbox/`, runs
`reader import` and reports the coverage. The commands behind both doors:

| Command | What it does |
|---|---|
| `reader profile --level A1` | Prints the context block for the adaptation prompt |
| `reader import` | Validates, measures and stores whatever is in `inbox/` |
| `reader texts` | Lists what has come in |
| `reader analyze file.txt --level A1` | Measures a loose text without storing anything |
| `reader serve` | Serves the reading interface at http://127.0.0.1:8000 |
| `reader export` | Writes the deck as CSV for Anki |

`analyze` exits with code 1 when a text misses the threshold — that is what lets the
loop know it has to try again. It accepts `-` to read from standard input and
`--known file.txt` (one lemma per line).

## How it works

The walkthrough of every screen is in
[`docs/how-it-works.md`](docs/how-it-works.md) — what the glossary is, how the
review queue decides what to show, and what each dashboard number claims. One
sentence each:

- **Library** — *Add a text* takes an address or a pasted article and adapts it
  on its own; *Import waiting texts* and *Or paste the document* are the doors
  into `inbox/` that need no terminal.
- **Reading** — clicking a word makes a word card, selecting a passage makes a
  sentence card. The **glossary** under the text is the vocabulary the adaptation
  chose to teach, and *Save all* sends the lot to the deck without resetting the
  schedule of anything already there.
- **Review** — the day's queue, driven by the keyboard: **space** reveals,
  **1–4** grade, **u** undoes. The daily limit on new cards (10 by default)
  counts first appearances, not gradings.
- **Dashboard** — the level ladder, the history, the retention, and the Anki
  export.

## Decisions that look like details

Adapting from the web is §14's `Adapter` with a second implementation, the
`ClaudeCliAdapter`, alongside `InboxAdapter`. §3 of the MVP put this in v2, and §13
says that the friction of the manual mode is the signal that v2 is worth it — the
signal came early.

**This is not the paid API.** The adapter calls the `claude` binary already installed
and logged in on your machine, so it spends the same plan as typing the command in the
terminal. The `total_cost_usd` the CLI reports is the API equivalent, not a charge.

- **The child process runs with no tools at all** (`--tools ""`). The article you
  send to be adapted is untrusted text; an agent holding `Write` and `Bash` while
  reading a hostile page is a different risk from a model with no hands. As a bonus
  it consumes ~3× less of your window, and it works around a real defect: running
  `/adapt` through `claude -p` **with** tools fails reproducibly on CLI 2.0.76
  (`API Error 400: tool_use ids must be unique`).
- **The prompt goes in through stdin.** As an argument it is silently truncated at
  Windows' command-line limit — the process exits 0 and prints nothing.
- **The answer is unwrapped before it is parsed.** The model fences the JSON in
  ```` ```json ```` however firmly you ask it not to. The CLI's `--json-schema`
  would be the right fix and today returns 400.

`/adapt` still exists as the manual path, and both doors use the same profile block —
if they diverged, adaptations would get subtly worse through one of them and nothing
would say so.

Static files are served with the content's fingerprint in the URL
(`app.css?v=fb6014d5`). Without it the browser keeps the old stylesheet and a CSS
change arrives as a broken screen — which is exactly what happened, and it is a bug
that looks like wrong CSS while being a stale cache.

The **Anki export** comes out of `reader export` or the button on the dashboard, and
both produce the same file byte for byte. The details that decide between importing
straight away and having to fight the dialog:

- **`#tags column:3`.** Without that line Anki reads the third column as a *field*,
  not as tags — and in a two-field note type it disappears.
- **Escaping comes before markup, never after.** With `#html:true` Anki reads every
  field as HTML, so an `&` in the translation has to become `&amp;` — but the `<br>`
  and `<i>` the app inserts have to survive literally. The other way round, the
  formatting shows up as angle brackets on every card.
- **`#deck` and `#notetype` only preselect "if they exist"**, says the manual. The
  deck name goes; the note type's does not, because the default is called something
  different in every language Anki ships (`--notetype` if you want it).
- The first column is the word, and Anki uses the first field as the note's identity
  — so re-exporting **updates** the same notes instead of doubling the deck.

Reference: [Text Files, in the Anki
manual](https://docs.ankiweb.net/importing/text-files.html).

## Known limits

`reader serve` listens on localhost only, and it should stay that way: **there is no
authentication in this app at all**, by decision of the MVP. Anyone who reaches the
port reads and changes the deck.

The app fetches any http(s) address you type, including ones on the local network.
Because only you type them, and the app only listens on localhost, that is acceptable
here — but it is a door that would not exist if there were ever a second user.

`import` never destroys your input: an invalid file goes to `inbox/rejected/` with an
`.error.txt` beside it saying why, and re-importing the same text does not duplicate
it (identity is the hash of the adapted English).

The gates, in the order they hold:

```
ruff format . && ruff check . && mypy && pytest
```

## Calibration

`data/bands.toml` holds the cuts that turn frequency into a CEFR level. They are
**calibrated guesses, not truth**: the NGSL is a general frequency list, not an
official CEFR map. After twenty texts or so, compare how hard they felt against the
measured coverage and adjust the file — with no code involved.

This has happened once already, and it is worth keeping as a warning. The NGSL
lemmatises `their`→`they`, `these`→`this`, `his`→`he`, `an`→`a`; spaCy does not.
Thirteen of the commonest words in English matched no entry at all, fell through to
the Zipf scale — which only had a floor down to B2 — and came out classified as
**B2**. Every text with "my" or "your" in it lost coverage for that reason. The fix
was four lines in `data/bands.toml`, with the floors taken from the median Zipf of
each NGSL band (A1 5.43, A2 4.96, B1 4.59), **without a line of code**. The tests in
`tests/test_calibration.py` pin it against the real data.

Another warning, about use: grammar level and vocabulary level are different things,
and coverage only measures the second. The text `exemplo/bees-adaptado.txt` was
written with A1 grammar — sentences of six to ten words, no subordination — and it
measures 74.7% at A1, 90.6% at B1 and 100% at B2. The subject is what carries the
vocabulary: `bee` alone is 6.1% of the text and it is B2. A topic does not become A1
just because the sentences got shorter.

## Data

The data directory is `data/`, and `GRADED_READER_DATA_DIR` overrides it — that is
what lets the tests run against a temporary directory without touching the real
lists.

- `data/ngsl.csv` — the **New General Service List** (2,809 words), from
  [newgeneralservicelist.com](https://www.newgeneralservicelist.com/new-general-service-list),
  by Browne, Culligan and Phillips. Licensed **CC BY-SA 4.0**. The published file is
  `NGSL_12_stats.csv`, kept here unchanged.
- `data/bands.toml` — the band cuts and the coverage thresholds.
- `data/levels.toml` — the grammar budget of each level, quoted in the prompt.

The database is a SQLite file (`graded-reader.db`, overridable with
`GRADED_READER_DB`) and the inbox with `GRADED_READER_INBOX_DIR`. Neither goes into
git.

Frequency for anything outside the NGSL comes from
[wordfreq](https://pypi.org/project/wordfreq/).

## Licences

The code is under the MIT License ([`LICENSE`](LICENSE)). Two files inside the
repository are **not** this project's work and carry their own terms — the NGSL list
in `data/` (CC BY-SA 4.0, which is *share-alike* and is not the code's licence) and
htmx in `src/graded_reader/web/static/` (0BSD). Both are set out in
[`NOTICE`](NOTICE).
