# Graded Reader — MVP document

A graded English reader with its own flashcards and a progress dashboard.
Specification document for implementation assisted by Claude Code.

**Version:** 1.0 — 2026-09-21
**Status of the decisions:** closed, except where marked `[open]`.

---

## 1. The problem

Reading in English only works as learning when the text sits a little above what the
reader already knows. Authentic material (news, articles, transcripts) almost never
does. The existing answers are either ready-made graded texts — few of them, dull,
about subjects nobody cares about — or translators, which teach nothing.

Graded Reader takes **any** text and rewrites it at the right level, pulls out the
vocabulary worth learning, and tracks what actually stuck over time.

**The v1 user:** one person (the author), a native speaker of Brazilian Portuguese
studying English. No authentication, no multi-user, no cloud.

---

## 2. The product thesis

Three claims the MVP exists to test:

1. **The level has to be verified, not requested.** An LLM told to "write at A1" gets
   it wrong silently. The guarantee comes from comparing the generated text against a
   frequency list, in code, and demanding a minimum coverage.
2. **The CEFR level is coarse; personal vocabulary is what matters.** The goal is to
   adapt for "the 1,000 words of A1 **plus** the 312 this user has proved they know",
   introducing five to eight new words per text (i+1).
3. **Learned vocabulary is the only progress indicator that does not lie.** Not
   "streak days", not "texts read": words that survive spaced repetition.

---

## 3. Scope of v1

### In

- Importing an adapted text (JSON) produced by Claude Code.
- Generating the adaptation prompt from the user's profile (level + vocabulary).
- Validating the imported text's level coverage, in code.
- Reading the adapted text in the interface, clicking words and saving them.
- Flashcards with spaced repetition (FSRS) and a keyboard-driven review screen.
- A dashboard with vocabulary growth, reviews and coverage by level.
- Exporting the deck to Anki (CSV).

### Out (v2 or later)

- Audio and video transcription (Whisper, `yt-dlp`).
- Calling the Claude API directly from inside the app.
- A phone app or phone synchronisation.
- Multi-user, login, server deployment.
- Generating exercises beyond the comprehension questions that come in the JSON.

---

## 4. The main flow

```
                      ┌──────────────────────────────┐
                      │  Claude Code (in the repo)   │
                      │  /adapt A1 article.txt       │
                      └──────────────┬───────────────┘
                                     │ 1. reads the profile
                    ┌────────────────┴────────────────┐
                    │  reader profile --level A1      │  ← the app's own CLI
                    │  (level, exceptions, i+1 target)│
                    └────────────────┬────────────────┘
                                     │ 2. adapts and writes
                              inbox/2026-09-21-greenland.json
                                     │ 3. imports and validates
                    ┌────────────────┴────────────────┐
                    │  reader import  /  button in UI │
                    │  coverage 96% A1 · 4 outside    │
                    └────────────────┬────────────────┘
                                     │
              ┌──────────────────────┴──────────────────────┐
              │  Web (localhost:8000)                       │
              │  read → click words → review → dashboard    │
              └─────────────────────────────────────────────┘
```

The important point: **the app calls no LLM at all in v1.** It generates the prompt,
receives the JSON and does all the deterministic work (validation, lemmatisation,
scheduling, statistics). What pays for and runs the intelligence is Claude Code, which
you already have open in the repository.

---

## 5. Technical decisions

| Area | Decision | Why |
|---|---|---|
| Language | Python 3.12+ | The linguistic ecosystem (spaCy, wordfreq) is the heart of the project |
| Web | FastAPI + Jinja2 + HTMX | Server-rendered, almost no JS; review needs a keyboard, not an SPA |
| Database | SQLite (a `graded-reader.db` file) via SQLModel | One process, one file, backup is `cp` |
| SRS | `fsrs` (py-fsrs) | Modern Anki's algorithm; do not reimplement SM-2 |
| Lemmatisation | spaCy `en_core_web_sm` | `running → run`, avoids duplicate cards |
| Frequency | NGSL (2,809 words) + `wordfreq` | NGSL covers A1–B1; wordfreq's Zipf covers the rest |
| CLI | Typer | `reader profile`, `reader import`, `reader analyze` |
| Tests | pytest | Only in the lexicon and SRS layers (see §11) |
| Formatting | ruff | — |

`[open]` If the web interface stalls progress, the emergency exit is to build v0 in
Streamlit and migrate later — but that costs the good review screen.

### Dependencies

```
fastapi, uvicorn[standard], jinja2, python-multipart, sqlmodel,
fsrs, spacy, wordfreq, typer, httpx, pytest, ruff
```
Plus the model: `python -m spacy download en_core_web_sm`.

---

## 6. The vocabulary layer (the core)

Module `graded_reader/lexicon.py`. It is the piece that gives the rest its value; build
it first.

### Level bands

Every word gets a band from two sources, in this order:

1. **NGSL**, by frequency rank (CSV file, licensed CC BY-SA 4.0 — the attribution goes
   in the README):
   - rank 1–500 → `A1`
   - 501–1000 → `A2`
   - 1001–2000 → `B1`
   - 2001–2809 → `B2`
2. **wordfreq**, by Zipf, for whatever is not in the NGSL:
   - Zipf ≥ 4.0 → `B2`
   - 3.0 ≤ Zipf < 4.0 → `C1`
   - Zipf < 3.0 → `C2`
3. Proper nouns, numbers and acronyms (detected through spaCy's POS) → `NA`, out of the
   count.

The cuts above are calibrated guesses; keep them in `data/bands.toml` so they can be
adjusted without touching code. After twenty texts or so, compare how hard they felt
against the measured coverage and recalibrate.

### Coverage

```python
def coverage(text: str, level: str, known: set[str]) -> CoverageReport
```
Returns: total tokens, tokens inside the target level, tokens outside it (with lemma,
band and frequency in the text), the coverage percentage, and the list of card
candidates (words outside the level **or** above the target band that are not in
`known`).

**Acceptance criterion for a text:** coverage ≥ 95% for A1/A2, ≥ 92% for B1/B2, ≥ 90%
for C1/C2. Below that, the UI marks the text "out of level" and suggests running
`/adapt` again. Do not block the import — show the number and let the reader decide.

---

## 7. The import file contract

This is the most important contract in the project: it is the border between Claude
Code and the app. A JSON file in `inbox/`, any name, UTF-8.

```json
{
  "schema": 1,
  "level": "A1",
  "title": "Iceland Turns Carbon Into Stone",
  "source": {
    "kind": "url",
    "value": "https://example.com/article",
    "original_text": "the whole original text, as it was pasted"
  },
  "adapted_text": "Paragraph one.\n\nParagraph two.",
  "glossary": [
    {
      "en": "underground",
      "pt": "subterrâneo",
      "example_en": "They put the gas deep underground.",
      "example_pt": "Eles colocam o gás bem fundo, no subsolo."
    }
  ],
  "questions": [
    { "q": "What do they put into the rock?", "a": "Carbon dioxide and water." }
  ],
  "prompt_used": "the complete prompt, for reproducibility",
  "generated_at": "2026-09-21T22:40:00-03:00"
}
```

Importer rules:
- `schema`, `level`, `adapted_text` are required; the rest is tolerated if missing.
- An invalid file goes to `inbox/rejected/` with an `.error.txt` beside it. Never
  destroy the user's input.
- A successfully imported file goes to `inbox/processed/`.
- Importing is idempotent by the hash of `adapted_text`: re-importing does not
  duplicate.
- After importing, run the coverage validation and store the result on the text's
  record.

---

## 8. Generating the prompt

The command `reader profile --level A1 [--new-words 8]` prints the context block that
Claude Code injects into the adaptation prompt. It contains:

- The target level and its grammar rules (a fixed table in `data/levels.toml`, one
  entry per level: vocabulary budget, allowed structures, sentence length).
- **Exceptions upward:** up to 40 words the user already knows that sit above the
  target level — they may be used freely.
- **The i+1 target:** five to eight words from the band immediately above, chosen among
  the most frequent ones the user does not yet have in the deck — they must appear in
  the text and in the glossary.
- **Reinforcement:** up to 10 words that are being learned and fall due within the next
  three days — if they fit naturally, they should reappear in the text.

**Do not send the level's whole word list in the prompt.** The model already has a good
sense of English frequency bands; what it does not have is your profile. The strict
check happens afterwards, in code.

The Claude Code slash command lives at `.claude/commands/adapt.md` in the repository
itself and describes these steps: run `reader profile`, read the source text, adapt it,
write the JSON into `inbox/`, run `reader import` and report the coverage.

---

## 9. Data model

```
word
  id              INTEGER PK
  lemma           TEXT UNIQUE NOT NULL     -- base form, lowercase
  display         TEXT NOT NULL            -- as it appeared in the text
  pt              TEXT
  example_en      TEXT
  example_pt      TEXT
  band            TEXT                     -- A1..C2, NA
  first_text_id   INTEGER FK -> text.id
  created_at      TEXT
  fsrs_json       TEXT NOT NULL            -- serialised Card (py-fsrs)
  due             TEXT NOT NULL            -- denormalised from the Card, to query on
  stability       REAL                     -- likewise, for the dashboard
  state           TEXT                     -- new | learning | review | relearning

review
  id              INTEGER PK
  word_id         INTEGER FK -> word.id
  rating          INTEGER                  -- 1..4 (Again, Hard, Good, Easy)
  reviewed_at     TEXT
  log_json        TEXT                     -- serialised ReviewLog

text
  id              INTEGER PK
  title           TEXT
  level           TEXT
  source_kind     TEXT                     -- url | file | paste
  source_value    TEXT
  original_text   TEXT
  adapted_text    TEXT
  glossary_json   TEXT
  questions_json  TEXT
  prompt_used     TEXT
  coverage_pct    REAL
  out_of_level    TEXT                     -- JSON: [{lemma, band, count}]
  content_hash    TEXT UNIQUE
  created_at      TEXT

setting
  key             TEXT PK
  value           TEXT                     -- current level, daily new-card limit, etc.
```

Storing the whole `fsrs_json` and denormalising `due`/`stability`/`state` avoids
reimplementing the FSRS model while still allowing the queue to be queried in plain
SQL. The `review` table is never deleted from: the entire dashboard comes out of it.

---

## 10. Screens

Three of them, at `localhost:8000`. Visual reference: the prototype already built
(three tabs, a serif typeface in the reading area, a dashboard with a level ladder).

**Reading** — the list of imported texts; opening one shows the adapted text in a
reading face, with the words already in the deck highlighted. Clicking a word opens the
side card (lemma, band, the glossary translation if there is one) with a save button.
Below it, the glossary with "save all", the comprehension questions with hidden
answers, and the coverage strip ("96% inside A1 · 4 words above").
Beside it, the original text reachable in a tab — re-reading the original after
understanding the adapted version is half the value of the method.

**Review** — one card at a time, centred. Front: the word and the example with the word
blanked out. Back: the translation, the full example, and the four FSRS buttons showing
the interval each one produces. **The keyboard is mandatory:** space reveals, 1–4
grade, `u` undoes the last grading. Without a keyboard, reviewing 40 cards is
punishment.

**Dashboard** — at the top, the numbers: words in the deck, mastered (stability ≥ 21
days), being learned, reviews today, 30-day retention.
Then **the ladder**: for each level, how many words of that band are already mastered,
over the size of the band. It is the honest version of "how much of A1 do I have" —
very different from counting cards.
Then the review history (bars, 30 days) and the retention curve FSRS predicts for the
next 30 days, which is the chart that shows the workload coming.

---

## 11. What to test

Tests only where a mistake is silent:

- `lexicon`: lemmatisation of irregular forms (`went → go`, `children → child`), band
  assignment, coverage computed against a fixed reference text.
- `importer`: valid JSON, broken JSON, re-importing (idempotency), missing fields.
- `srs`: a sequence of gradings produces growing intervals; `Again` knocks it down;
  serialising and deserialising the `Card` survives a round trip through the database.

Do not write route or template tests in the MVP.

---

## 12. Build milestones

Each milestone is usable on its own and has a verifiable done criterion. Build and test
one at a time.

**M0 — Lexicon (the core, no interface)**
Project, dependencies, NGSL downloaded into `data/`, `lexicon.py` complete.
*Done when:* `reader analyze article.txt --level A1` prints the coverage and the list of
out-of-level words, and the lemmatisation tests pass.

**M1 — The loop closed, from the terminal**
Schema and database, `reader profile`, importer with validation,
`.claude/commands/adapt.md`.
*Done when:* you run `/adapt A1 article.txt` in Claude Code and the text lands in the
database with its coverage computed, without touching any interface.

**M2 — Reading**
FastAPI, the list and reading screens, clicking a word, saving to the deck, the
glossary, the questions.
*Done when:* you read an imported text and finish with eight words in the deck.

**M3 — Review**
py-fsrs integrated, the day's queue, the keyboard-driven review screen, undo, the daily
limit on new cards.
*Done when:* you review three days running and the intervals behave.

**M4 — Dashboard**
The numbers, the level ladder, the history, the retention curve.
*Done when:* the dashboard answers "how many A1 words do I know" with a number you
believe.

**M5 — Anki export**
CSV with `term, translation<br><i>example</i>, tags` and the headers `#separator:Comma`,
`#html:true`.
*Done when:* the file imports into Anki with no manual adjustment.

After M4, stop and use it for two weeks before writing a line of v2. Band calibration
and the daily new-card limit only show themselves under real use.

---

## 13. Known risks

**Lemmatisation will get things wrong.** Irregular forms and phrasal verbs (`give up` ≠
`give`) will produce strange cards. Mitigation: allow editing a card's lemma in the
interface, and treat multi-word expressions as a single card when they come from the
glossary.

**The level bands are an approximation.** The NGSL is a general frequency list, not an
official CEFR map. The rank → level correspondence is calibratable on purpose; do not
treat it as truth.

**The manual mode has friction.** If running `/adapt` every time becomes annoying, that
is exactly the signal that v2 with an API is worth it — and the `Adapter` interface will
already be there.

**Too many cards too early.** Saving 12 words per text turns into 300 due cards in a
month and abandonment. Default limit: 10 new cards a day, adjustable in the settings.

---

## 14. Designing for v2 (what not to get wrong now)

A single abstraction in the MVP, defined in `graded_reader/adapters/base.py`:

```python
class Adapter(Protocol):
    def adapt(self, source_text: str, profile: Profile) -> AdaptedText: ...
```

`InboxAdapter` in v1 (reads from `inbox/`). `ApiAdapter` in v2 (calls the Claude API
with the same `Profile` and returns the same `AdaptedText`). Nothing else needs
abstracting — resist creating layers for the database, for rendering or for
transcription before a second real use case exists.

For v2 on the phone, what matters is that the business rules live in pure modules
(`lexicon`, `srs`, `profile`) and not inside the FastAPI routes. If that is respected,
exposing a JSON API later is a day's work.

---

## Sources

- FSRS in Python: https://github.com/open-spaced-repetition/py-fsrs (the `fsrs` package)
- New General Service List: https://www.newgeneralservicelist.com/new-general-service-list
  (2,809 words, CC BY-SA 4.0)
- wordfreq: https://pypi.org/project/wordfreq/
