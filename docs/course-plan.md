# The course plan

**Status:** direction decided on 2026-09-28. Everything under
[Open questions](#open-questions) is still open, and nothing here is code yet.

This document replaces the separate v2 and V3 documents. Their ideas are not lost —
they are [reclassified below](#what-happens-to-the-v2-and-v3-ideas), each against the
goal that now drives the project.

---

## What changed, and why

After the MVP was built, the app was not being used — and that is the most useful
result the project has produced so far. §12 of the [MVP](graded-reader-mvp.md) asked
for two weeks of real use before writing v2 precisely so that this kind of thing would
surface.

It surfaced a problem of shape, not a bug. The app is a **tool**: it waits for you to
arrive with material. Every day, you do the teacher's job — find an article, decide
whether it is worth it, pick the level, decide what to study — and the app only
executes. That is not productive, and it is not a habit anyone keeps.

What is wanted is a **course**: you open it, and it already knows what you do today.
It decides the content, the order and the pace, and it shows where you are, where you
are going, and roughly how long it will take.

**The engine that exists is the right engine for a course.** Bands, coverage
measurement, adaptation by level, FSRS, the dashboard ladder — all of it stays. What was
missing was not the engine but the driver. The change is in who takes the initiative.

---

## The goal

**English good enough to work abroad as a software developer, and to pass the
interviews that get you there.**

The default target is **B2** — the level international job postings usually mean when
they ask for English to work in. It is a setting, not a constant.

### Why this goal makes "how long" answerable

When the target was "maximum fluency", no honest deadline existed: past B2, vocabulary
has no end, which is why the dashboard already refuses to give C1 and C2 a percentage.
B2 fits entirely inside the NGSL, which is finite and countable — 500 words at A1, 500
at A2, 1,000 at B1, 809 at B2.

So the app can measure how many words **you** actually consolidate per day and turn the
words left into a date. For example: 500 A2 words left, at seven consolidated a day,
is about ten weeks. A number built from you, not from a table.

Two conditions keep that number honest:

- **It only appears after about two weeks of use.** Until then the app does not know
  your rate, and a date on day one is a guess dressed up. The screen says "measuring".
- **It is a deadline for vocabulary, and says so.** Speaking and listening are tracked
  by other signals, without a single date.

---

## Decisions

| # | Question | Decision |
|---|---|---|
| 1 | What is the course for? | **Work English and job interviews in software development** |
| 2 | Target level | **B2**, editable |
| 3 | Where does content come from? | **Mixed:** generated work situations, plus real news on your topics |
| 4 | Topics | Sports, technology, games, software development — **an editable list** |
| 5 | Time per day | **A setting** — 30 minutes is an example, not a rule |
| 6 | Where does it run? | **Only on this computer, for now.** Localhost, no authentication |
| 7 | Spending the Pro plan | **Allowed whenever the server is running**, through `claude -p` |
| 8 | Speaking practice | Turn-based first, real-time later; `claude -p` first, the API later |
| 9 | What the tutor gives feedback on | **Vocabulary** — to revisit for interviews (see open questions) |

Decision 6 keeps the biggest cost out of the way: no login, no HTTPS, no deployment.
The course runs where the app already runs.

---

## The course, day to day

### Setting up, once

- **The goal** — B2 by default.
- **The time per day.**
- **The topics** for the real news.
- **A placement test.** Today you *declare* A1. A course has to *measure*. The
  well-known, cheap way is a vocabulary size test: the app samples words from each
  NGSL band, you say which ones you know, and it estimates how many words you have per
  level. It is deterministic and fits the existing engine. Afterwards the level keeps
  recalibrating itself from your reviews.

### The Today screen

You open the app, and it is ready:

1. **Due reviews first.** They are a fixed cost, so they come before anything new. They
   also work as the loading screen: if the day's text is not ready yet, it is generated
   in the background while you review — and a review block lasts longer than
   `claude -p` takes.
2. **The text of the day, with audio.** Alternating between a generated work situation
   and adapted real news. Listening before reading is itself an exercise.
3. **Exercises on the text**, vocabulary first: comprehension, a cloze from the text,
   writing a sentence with a target word.
4. **A summary:** what you did, what is worth saving, and how far you moved.

At the end of the session, the app prepares tomorrow's content while the server is on.

### The time budget drives the load

The app can measure how long **you** take per card, from the timestamps every review
already records. So the budget splits itself: reviews first, and what remains decides
how much new material fits.

**The fixed limit of 10 new cards a day goes away.** The number of new cards comes out
of the time you set. That also retires the "+3 waiting" problem at its root: the limit
stops being an arbitrary number that hides cards.

### When a day is skipped

Reviews pile up, and they still come first. New material shrinks to fit what is left of
the budget, down to nothing on a heavy day. The course never tries to "catch up" by
doubling tomorrow — that is how a backlog becomes abandonment.

---

## The content

### Mixed, and each half has a job

| Kind of text | What it covers | What it is for |
|---|---|---|
| **Generated** | Work situations: a stand-up, a code review, an email to your manager, an interview question | **Moving you toward the goal** — this is the course's syllabus |
| **Real news, adapted** | Sports, technology, games, software development — an editable list | **Making you come back tomorrow** — this is the motivation |

The generated text carries the syllabus; the news carries the interest. Neither one
alone would do: generated-only is the dull graded reader §1 of the MVP complained about,
and news-only never reaches the vocabulary an interview needs.

Real news comes from **feeds you choose per topic**, fetched and adapted by the app.
The adaptation path already exists — the web adapter adapts any URL. What is new is the
app choosing the URL itself.

### The syllabus is a ladder of work situations

Designing a curriculum from scratch was the biggest risk of this plan, and it is
pedagogy, not code. The goal gives it a spine: **each level is a set of situations you
will actually face**, not an abstract list of grammar.

| Level | Situations, for example |
|---|---|
| A1–A2 | Introducing yourself, saying what you do, describing your day at work |
| B1 | Explaining a bug, speaking at a stand-up, asking for help, writing a clear message |
| B2 | Disagreeing in a code review, negotiating a deadline, the job interview itself |

The grammar in `data/levels.toml` still holds underneath — it is what the generator is
told it may use at each level. The situations are what you see.

### A developer's vocabulary breaks the bands

The NGSL is general English. Words like `deploy`, `merge`, `refactor` and `stakeholder`
land at C1 by frequency — and for a developer they are day-one words. A technical text
would be marked out of level because of vocabulary you already own.

- **The placement test includes technical vocabulary**, so those words come in as known.
- **A work-vocabulary list** would give the work situations a real target. The same
  authors as the NGSL publish a **Business Service List**; its licence has to be checked
  before it is used here.

---

## Measuring progress

- **Where you are:** the placement test, then your reviews, per band.
- **Where you are going:** the target — B2.
- **How long:** a date for vocabulary, built from your measured rate, after two weeks of
  data (see [the goal](#why-this-goal-makes-how-long-answerable)).
- **Readiness on real material:** the app takes real, unadapted news and computes how
  much of it your vocabulary already covers. "You already understand 94% of a tech news
  article with no adaptation" is a readiness signal no deadline can give, and it is
  computed by the `coverage()` that already exists.

The rule the dashboard was built on still holds: a confidently wrong number is worse than
no number, because nothing on the screen says to doubt it. Every measure above says what
it is about.

---

## Speaking: interview practice

The V3 speaking tutor stops being a separate mode and becomes **interview practice
inside the course** — and it moves up in priority, because an interview is spoken.

What carries over from the V3 design:

- **Turn-based**, like a walkie-talkie: you speak, you stop, the tutor answers.
- **`claude -p` with no tools at all.** Adapted news goes into the tutor's prompt, and a
  hostile page is a prompt-injection attempt aimed at a model with no hands.
- **The tutor's replies are measured, not trusted.** Each one goes through `coverage()`
  before it is spoken, as an imported text does.
- **Per-level behaviour comes from `data/levels.toml`**, not from a second definition of
  what "A2" means.
- **An echo is not a use.** If the tutor said "enormous" and you answer "yes, enormous",
  you repeated, you did not produce. Only unprompted use counts as evidence.
- **Nothing is saved automatically.** The session ends with a summary and save buttons.
- **Grammar is recast, not corrected**, in everyday practice.
- **Speech recognition is local** (`faster-whisper`), and **no audio is stored** by
  default — only the transcript.

The modes become: discussing the text of the day, free conversation on your topics, and
**interview simulation** — introducing yourself, behavioural questions, explaining a
project you built.

### Latency is still the risk

`claude -p` costs seconds just to start. Whether a turn fits in a bearable time has to be
measured before anything is built on it: a throwaway script timing one turn with a
growing transcript, and `faster-whisper` on a ten-second clip on this machine. That
spike is cheap and independent, and can run at any point. If a turn goes past roughly
eight seconds, a conversation stops feeling like one, and the move to the API — **pay
per use, not the Pro plan** — is decided with the number in hand.

---

## Phases

Each phase has to change what you do, not only what the app can do. The same shape as
the MVP's milestones: usable on its own, with a done criterion you can check.

**Phase 1 — The Today screen.**
Time and topics settings, the day's session (reviews, a text with audio, exercises, a
summary), content prepared ahead in the background, generated work situations
alternating with adapted news, and archiving old texts so a daily course does not bury
the Library.
*Done when:* you open the app on most days for two weeks because it is ready, not
because you remembered to feed it.

**Phase 2 — Knowing where you are.**
The placement test with technical vocabulary, the new-card load driven by the time
budget, and the goal line on the dashboard.
*Done when:* the dashboard says where you are, where you are going, and — after two
weeks of data — how long it will take, for vocabulary.

**Phase 3 — The syllabus.**
The ladder of work situations driving what gets generated, and readiness measured on
real news.
*Done when:* a week of generated texts follows the ladder, and you can tell what level
of situation you are working on.

**Phase 4 — Speaking.**
The latency spike first. Then the tutor typed, then with voice, then the vocabulary loop,
then interview simulation.
*Done when:* you get through a spoken mock interview at your level and end it with a list
of words you used, missed, and can save.

**Later:** real-time conversation, the API backend, pronunciation assessment, active
vocabulary as a second layer on the ladder, and the phone.

---

## What happens to the v2 and V3 ideas

| Idea | Before | Now |
|---|---|---|
| Audio for the text | A play button | **Central** — every text of the day comes with audio (phase 1) |
| Speaking tutor | A separate V3 mode | **Interview practice** inside the course (phase 4) |
| Deleting texts | Needed | **Archiving**, because the course generates texts daily (phase 1) |
| Deck browser | New screen | Still useful, unchanged — not tied to a phase |
| Review looking like Anki | A list of pieces | The Today screen takes over the entry point; suspend and coloured counters stay candidates |
| PDF and EPUB | A main new source | **A side door**, "bring your own material" — a passage, not whole books |
| YouTube with subtitles | A main new source | **Parked** — may return as listening practice with technical talks |
| Rebuilding X and Reddit pages | A main new source | **Parked** — and if it returns, as the conversation's structure, never the page's HTML |
| *Add a text* and `/adapt` | The front door | **Optional extra reading** |

The reasoning behind the parked ones still holds and is worth keeping for whenever they
return:

- **Deleting or archiving a text** runs into a foreign key nothing enforces:
  `word.first_text_id` points at `text.id`, and SQLite runs with the check off (the
  default, and nothing in the app turns it on). The recommendation stands: drop the
  reference, and turn on `PRAGMA foreign_keys = ON` at the same time.
- **Adapting subtitles destroys their timing.** Rewriting joins and cuts sentences, so the
  adapted text can no longer follow the video. Adapt the whole thing and show it beside
  the video, without syncing.
- **Returning a third party's HTML inside the app is XSS by construction**, in an app with
  no authentication — and X and Reddit are blocked by access, not by code.
- **A book is not one more input format.** It is 80,000 words against a 2,000-word text
  limit, and it needs the notion of a work split into chapters.

---

## Open questions

| # | The question | Recommendation |
|---|---|---|
| 1 | How do generated texts and news split across days? | Alternate day by day to start, then look at which ones you actually finish |
| 2 | Which feeds per topic? | You choose them; the app ships with none rather than guessing your taste |
| 3 | Does the interview tutor give feedback on grammar too? | Probably yes, but only when an error blocks understanding — an interviewer judges whether you were understood |
| 4 | Should speaking a deck word correctly count as an FSRS review? | **No.** FSRS is calibrated on card reviews; feeding it another kind of evidence moves every schedule on a guess |
| 5 | What happens when the Pro plan limit is hit mid-day? | The session degrades instead of breaking: reviews and material already prepared still work |
| 6 | Can the Business Service List be used? | Check its licence before anything depends on it |
| 7 | At what latency per turn does speaking move to the API? | Decide after the spike, with the number in hand |

---

## Risks

**Scope.** "A course from A1 to B2" is the size of a product company's app. The answer is
the phases: each one has to change your behaviour before the next is built.

**Generated content can be bad.** The product thesis applies here harder than anywhere:
the level is verified, not requested. Every generated text goes through `coverage()`
before it is shown, exactly as an imported one does.

**Pedagogy is not code.** The ladder of work situations is what keeps this from becoming
curriculum design from scratch — the goal chooses the situations.

**The habit is the real test.** If the Today screen does not bring you back, nothing
built after it matters. That is why it is phase 1, and why its done criterion is about
you, not about the code.
