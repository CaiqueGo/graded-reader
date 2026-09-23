# How it works, today

This document describes the system **as it is**, not as it was planned. The full
specification, with the decisions and what was left for later, is in
[`graded-reader-mvp.md`](graded-reader-mvp.md).

## The loop

Everything turns around a four-step cycle. Each screen of the app is one of them.

```
    adapt              read             review            measure
  ---------        ----------        -----------        ----------
  any text   -->   at your     -->   what you     -->   whether it
  at all           level             saved              is sticking
      ^                                                      |
      +------------------------------------------------------+
             the dashboard says when the level can go up
```

The app does the deterministic part — measuring, lemmatising, scheduling, counting.
Rewriting the text belongs to Claude Code. What you learn comes out of what **you**
choose to save while reading.

## Map of the screens

| Screen | Address | What for |
|---|---|---|
| Library | `/` | Adding texts and opening the ones already in |
| Reading | `/texts/{id}` | Reading, saving words, saving sentences, the glossary |
| Review | `/review` | The day's queue |
| Dashboard | `/dashboard` | Whether it is working, and the Anki export |

Start it with `reader serve`, at http://127.0.0.1:8000.

## Three words that turn up on every screen

**Lemma** is the base form of a word: `machines` and `machine` are the same lemma,
`machine`. The deck stores lemmas, which is why saving `machine` makes `machines`
show up highlighted in the text.

**Band** is the word's estimated CEFR level — A1 to C2. It comes from how often the
word appears in English in general, not from an official CEFR list; the cuts live in
`data/bands.toml` and can be recalibrated without touching code.

**Coverage** is the share of a text's words that sit at your level or below. It is the
number that decides whether a text is readable for you. It measures **vocabulary, not
grammar** — a text can measure 95% and still carry more subordination than an A1
reader can follow.

---

## Library: where texts come from

Three doors, all leading to the same place:

- **Add a text** — paste an address or the whole article, pick the level, and the app
  adapts it on its own. This is the normal path.
- **Import waiting texts** — reads whatever is sitting in the `inbox/` directory. This
  is what the terminal's `/adapt` command uses: it writes a JSON there and you import.
- **Or paste the document** — paste the adapted JSON straight into the browser.

Nothing is destroyed when something goes wrong. An invalid document goes to
`inbox/rejected/` with an `.error.txt` beside it explaining why. Re-importing the same
text does not duplicate it: identity is the hash of the adapted English.

A text that lands below the coverage threshold **comes in anyway**, marked `out of
level`. The app shows you the number and leaves the decision with you.

---

## Reading: where the deck is born

The screen holds the adapted text, with the original one click away in the next tab.

### Clicking a word

Opens a side panel with the translation and the example, when the text brought them,
and a button to save. It becomes a **word card**. Words already saved show up
highlighted in the text, inflected forms included.

### Selecting a passage

Drag the mouse across two words or more and **Save this sentence** appears. You pick
which word in the passage is the target, write what the sentence means, and it becomes
a **sentence card**.

The cut is exactly what you selected — a clause, an expression, half a line. The
target word is **marked, not removed**: the point is to read the sentence and know
what it says, not to fill in a blank.

When you choose the target, the words the text flagged as above your level come first
— they are the ones that probably made you select that passage.

### The glossary

Under the text sits the **glossary**: the list of words the adaptation decided to
teach in that text, with a translation and an example sentence.

The one choosing is the adapter, at the moment the text was processed, looking at your
profile: what you already know, what is about to slip, and how many new words fit. An
adapted text usually brings between 10 and 30 entries. The glossary is stored with the
text — it is not recomputed, and it does not change when your deck changes.

What the screen adds is one thing only: which of those words are **already in your
deck**, marked `in deck`.

**Save all** saves every one of them at once, as word cards. The ones already in the
deck are left exactly as they are, **schedule included** — clicking twice does not
restart anything's timetable.

### A glossary and a sentence card are not the same thing

|  | Glossary | Selecting a passage |
|---|---|---|
| Who chooses | the adaptation, before you read | you, while reading |
| Front of the card | the word, with the example as a cloze | the sentence, with the word marked |
| Back | the word's translation | what **you** wrote |
| Cost | one click for twenty words | one per sentence |

The glossary is the fast path and the words are the adapter's choice. Selecting is
slow and it is yours. The two live side by side: the same word can have a word card
and one or more sentence cards.

---

## Review: the day's queue

The top of the screen is a scoreboard:

```
1 due   9 new   +3 waiting   1 done today
```

| Counter | What it is |
|---|---|
| `due` | Cards that have come round and need answering today |
| `new` | New cards that still fit **within today's limit** |
| `+N waiting` | Cards you saved that the limit is holding back |
| `done today` | Gradings since midnight |

### The keyboard is the interface

**space** reveals the answer. **1–4** grade: Again, Hard, Good, Easy. **u** undoes the
last grading. The mouse works, but nobody who reviews daily uses it.

The intervals written on the buttons carry a `~` on purpose. FSRS fuzzes the intervals
so that eight words saved from the same text do not come back together forever — the
number is the order of magnitude, not the promise.

### Undo really undoes

`u` restores the card from a snapshot taken at grading time, and **deletes the review
row**. A misclick does not become history: left there, it would dirty the retention
curve and the day's count.

### The daily limit on new cards

**10** by default, adjustable on the review screen itself. It counts **first
appearances**, not gradings — a card reviewed four times today spent one slot, not
four.

The limit exists so you do not wake up to 300 due cards in a month and abandon the
deck. But it hides things, which is why the screen says how many are waiting and why.
New cards come out **oldest first**: a backlog gets worked through rather than buried
under fresher arrivals — so saving a sentence today does not jump the queue ahead of
yesterday's words.

### Editing and adding

You can **add a word** straight to the deck without going through any text, and
**correct the card** at the moment it comes up — the lemma included, which is the known
weak point of lemmatisation (`give up` is not `give`). Correcting the lemma does not
touch the schedule: it is the same card. Renaming it to a lemma that already exists is
refused, because merging two histories is your decision.

---

## Dashboard: whether it is working

The dashboard answers one question, and it is **not** "how many cards do I have".

### The five numbers

| Number | What it means |
|---|---|
| **In the deck** | Cards saved, in total |
| **Learned** | Cards with stability ≥ **21 days** — the ones you will probably still have in three weeks |
| **In flight** | Still learning or relearning, not settled |
| **Reviewed today** | Gradings since midnight |
| **Retention** | How many you got right, out of the real recall attempts of the last 30 days |

"Learned" is a claim about the future, and the app uses the same 21-day constant to
say it on the dashboard and to build the list of words the adapter may treat as known.
One definition, in both places.

### The ladder

For each band, how many words of that band you have learned over how many words the
band holds: **58 out of 500 at A1 is 11.6%**, and that is information. "140 cards in
the deck" is not.

The bar has two layers: what is in the deck and, stronger, what already counts as
learned. The denominator comes from the word list, so recalibrating `bands.toml` moves
the ladder with it.

**C1 and C2 have no bar.** They come from an open-ended frequency scale; there is no
total to divide by, and printing one would be inventing it. You see the count, not the
percentage.

### The two charts

SVG drawn by the app, no library. **History**: bars for the last 30 days.
**Retention**: for each of the next 30 days, the average chance you will recall a card
— along with how many fall due that day.

The curve uses **only cards that have been graded**. A card never reviewed has no
memory to decay, and averaging it in as either 0 or 100% would move the line without
meaning anything.

The retention axis runs from **0 to a full 100%**. Cutting it at 80% would turn a
gentle decline into a cliff. Every mark carries a `<title>` on hover, and every chart
has a table underneath for whoever wants the exact number.

### The dashboard refuses to answer two things

**Retention stays blank** until there are real recall attempts. Learning steps are
minutes apart; counting them as retention inflates the number without saying anything.

And, as above, **C1 and C2 get no percentage**. A confidently wrong dashboard is worse
than no dashboard, because nothing on the screen tells you to doubt it.

---

## Exporting to Anki

Through the **Export for Anki** button on the dashboard or through `reader export` —
both produce the same file, byte for byte.

Three columns: the word (or the sentence), the translation with the example in
italics, and the tags. The tag says which kind of card it is (`graded-reader word` and
`graded-reader sentence`), so you can treat the two differently in Anki.

Re-exporting **updates** the same notes instead of doubling the deck: Anki uses the
first field as the note's identity.

A card saved by clicking a word comes out with an empty back. The command tells you
how many are like that.

---

## What the system does not do

Worth knowing before trusting it too far:

- **There is no authentication.** `reader serve` listens on localhost only, and it
  should stay that way. Whoever reaches the port reads and changes the deck.
- **Coverage does not measure grammar.** Only vocabulary.
- **The bands are calibrated guesses**, not official CEFR truth.
- **Lemmatisation gets phrasal verbs wrong.** `give up` becomes `give`. You can
  correct the lemma on the review screen.
- **The adapter fetches any http(s) address you type**, including ones on the local
  network. Acceptable because only you type them — it would not be, with a second
  user.
