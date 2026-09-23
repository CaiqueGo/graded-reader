# Ideas for v2

**Nothing here is decided.** It is the list of what has been raised, with what already
exists in the code, what is missing, and the question that decides each one. §12 of the
[MVP](graded-reader-mvp.md) says to use the app for two weeks before writing a line of
v2 — the database was emptied on 2026-09-22, and that is the clock.

The order below is by cost, not by importance.

---

## 1. Deleting texts in the Library

**It does not exist today.** There is no route and no function in `library.py`: an
imported text stays forever.

The work is small, but there is a decision inside it that is not. The card stores
`word.first_text_id` as a **foreign key** to `text.id`, and SQLite runs with the check
**turned off** (that is the default, and nothing in the app turns it on). Deleting a
text today would leave cards pointing at a ghost, silently.

Three ways out, and the choice is a product one:

| Way out | What happens to the cards |
|---|---|
| Drop the reference (`SET NULL`) | They stay, with no origin. The deck is yours; the text was only where you found the word |
| Delete them too (`CASCADE`) | They go with the text, review history included |
| Refuse while cards exist | You decide card by card first |

**Recommendation:** drop the reference. The deck is the asset; the text is scaffolding.
And turn on `PRAGMA foreign_keys = ON` at the same time, or the next foreign key will
repeat the problem.

---

## 2. Audio for the text

Listening while reading is the classic win of a graded reader, and the first version is
nearly free: the browser has `speechSynthesis` built in — no dependency, no API, no
cost, works offline.

What that buys: a play button on the text, another on the sentence card, and the
highlight following the sentence being read (the browser's own `boundary` event gives
the position).

**What decides it:** whether the system voice is good enough for your ear. If it is
not, the next step up is a TTS API — and then there is a cost per character, and it is
worth keeping the audio on disk, because the same text re-read should not pay twice.

---

## 3. Review looking more like Anki

This needs to become a concrete list before it becomes code — "look like Anki" is about
six different things, and we already have some of them:

| Piece | Situation |
|---|---|
| Keyboard, four grades, intervals on the buttons | **exists** |
| Undo | **exists** |
| Editing the card during review | **exists** |
| Daily limit on new cards | **exists** |
| **Deck browser** (see, search, filter, delete) | missing |
| **Suspend and bury** a card | missing |
| Counters split by type, with colour (new / learning / due) | missing (three plain numbers today) |
| A study home screen before the queue | missing |
| Note types and card templates | missing, and probably should stay that way |

**Recommendation:** start with the **deck browser**. It is the most obvious gap in the
app today — there is no way to answer "what do I have?", to find that bad card you
remember making, or to delete one. Everything underneath already exists
(`words.all_cards`, the editing, `CardKind`); the page is what is missing.

Then **suspend**, which is what you do with a card that is not worth it and that you do
not want to delete.

**What not to copy:** note types and templates. It is the part of Anki that generates
the most configuration and the least studying, and here the two kinds of card come out
the way you saved them.

---

## 4. PDF, EPUB and other files

Extraction is the easy part (`pypdf` or `pdfminer` for PDF, `ebooklib` for EPUB). The
problem is the data model: today **one text is one article**, and the limit is 2,000
words (`sources.MAX_WORDS`). A book has 80,000.

So this is not "one more input format", it is the notion of a **work divided into
parts**: a chapter becomes a text, the chapters know about each other, reading
remembers where it stopped, and the Library groups them instead of listing 40 loose
items.

**What decides it:** whether what you want is to read books or just to pull a passage
out of a PDF. If it is the second, the path is far shorter — accept the file, extract
it, and let you choose the piece.

---

## 5. A YouTube video with adapted subtitles underneath

The video embedded in the page and the subtitles at your level below it.

Pulling the transcript is solved (`yt-dlp` fetches the subtitles, auto-generated ones
included). The real problem is elsewhere, and it is better faced before starting:

**adapting destroys the alignment.** Subtitles arrive in timed cues (`00:01:12 -->
00:01:15`). Adaptation rewrites the whole text — joining sentences, cutting others,
swapping words — and the result has no way of knowing which piece belongs to which
second. Which means: either the subtitle follows the video, or it is at your level.
Both at once requires picking one of two designs:

- **Adapt cue by cue**, preserving each one's timing. The subtitle really follows the
  video. The text gets worse — the adapter loses sight of the whole and cannot
  reorganise anything.
- **Adapt the whole thing and do not synchronise.** The text stays good, appears beside
  the video as an article, and you read it before or after watching. No karaoke.

**Recommendation:** the second, because it respects what the app already does well —
and because "read the adapted text, then watch the original" is a better exercise than
a subtitle racing past.

---

## 6. Rebuilding the page with the text swapped (X, Reddit)

The idea: fetch the page, swap the text for text at your level, and give the page back
**looking like itself**, so social media threads can be read adapted.

It is the most ambitious on the list and the only one I have a serious reservation
about — on three fronts:

**Security.** Returning a third party's HTML inside your app is XSS by construction.
This is not hypothetical: the app already fetches any address you type, and the only
thing holding that safe is that the fetched content becomes **text**, never markup.
Keeping the original markup means sanitising hostile HTML, which is a discipline of its
own, and doing it in an app with **no authentication at all**.

**Access.** X and Reddit are not ordinary pages. X requires a login and blocks
anonymous reading. Reddit has public JSON, but with terms that restrict automated use.
This idea is not blocked by code — it is blocked by access, and no line written here
unblocks it.

**Value.** In a thread, what matters is not the CSS: it is **who said what, replying to
whom**. That is structure, and structure can be preserved as data.

**Counter-proposal:** instead of rebuilding the page, store the **structure of the
conversation** — author, order, nesting — and render it in your own reading screen,
with each post adapted to your level. You get the thread readable, with the rest of the
app working on top of it (click a word, save a sentence), and without inheriting
anyone's HTML. What is left missing is only the site's appearance — which is precisely
the part that teaches no English.

---

## Open questions

Each item above runs into a choice that no code resolves. They are gathered here to be
answered in one go, whenever they are:

| # | The question | Recommendation |
|---|---|---|
| 1 | What happens to the cards of a deleted text? | Drop the reference, and turn on `PRAGMA foreign_keys = ON` |
| 2 | Is the browser voice enough, or will this need paid TTS? | Start with the browser's; only pay if your ear complains |
| 3 | Which pieces of Anki matter? | Deck browser first, suspend next; note types never |
| 4 | Read whole books, or pull a passage out of a PDF? | A passage, until there is a reason for chaptered works |
| 5 | Synchronised subtitles or well-adapted text? | Well-adapted text, no karaoke |
| 6 | Rebuild the page or store the conversation's structure? | Structure, always |

None of them has to be answered now. All of them get easier after two weeks of real
use.

## Suggested order

1. **Deleting texts** — days, and it fixes an integrity flaw that already exists
2. **Audio** — days, and the best value for effort on the list
3. **Deck browser** — the biggest gap in the app today
4. **PDF/EPUB or YouTube** — weeks, and the choice depends on what you read
5. **Threads** — only after deciding on structure instead of rebuilding

Outside the list, but circling it: **the phone**. It was not asked for, and it is still
where reading actually happens. Except that leaving localhost means authentication,
HTTPS and deployment — a declared project, not a lean-to.
