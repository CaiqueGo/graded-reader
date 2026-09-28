# V3 — a speaking tutor

**Status:** direction decided, design open. The four choices below were made on
2026-09-28; everything under [Open questions](#open-questions) is still open. Like
[v2](v2-ideas.md), nothing here is code yet.

---

## Why

Everything the app trains today is **recognition**: you read, you see the word, you
remember what it means. The card goes from front to back. Speaking is **production**,
the opposite direction — you have the idea and have to find the word. They are
different skills, and anyone who has only studied by reading knows the feeling of
understanding everything and being able to say nothing.

So the tutor is not one more feature. It is the other half of vocabulary.

## What makes it this app's tutor

Conversation chatbots for English are everywhere. What none of them has is what this
app already knows about you:

- **Conversation at your verified level.** The same profile block `/adapt` uses —
  known words, i+1 targets, words about to fall due — goes into the tutor's prompt.
  The tutor steers the conversation so you get to *use* the words you are learning.
- **The loop goes back to the deck.** A deck word you used correctly while speaking is
  evidence you own it. A word you reached for and did not have — you stalled, said it
  in Portuguese, talked around it — is a card candidate. The conversation feeds the
  deck, and the deck feeds the conversation.
- **The tutor's own speech is measured, not trusted.** The product thesis in §2 of the
  [MVP](graded-reader-mvp.md) is that a level has to be verified, not requested. That
  applies to the tutor as much as to adapted texts: every reply goes through
  `coverage()` before it is spoken, exactly as an imported text does.

---

## Decisions

| # | Question | Decision |
|---|---|---|
| 1 | Turn-based or real-time? | **Turn-based first.** Real-time, with interruption, later |
| 2 | Which feedback matters most? | **Vocabulary** |
| 3 | Plan or paid API? | **`claude -p` first**, on the Pro plan. The API later |
| 4 | Talk about a text, or talk freely? | **Both**, at every level from A1 to C2 |

### 1. Turn-based

Walkie-talkie style: you speak, you stop, the tutor answers, you speak again. It
tolerates a few seconds per reply, which is what makes `claude -p` a candidate at all.
Real-time conversation — the tutor listening while you talk, being interrupted,
answering in under a second — is a different project, with streaming speech in both
directions, and it comes after this one works.

### 2. Vocabulary as the feedback

Grammar and pronunciation are not ignored, but they are not what the tutor grades.

- **Grammar is recast, not corrected.** When you say something wrong, the tutor uses
  the right form naturally in its reply, the way a patient native speaker does. No
  red ink, no interruption.
- **Pronunciation is out of the first version.** A transcript says *what* you said,
  not *how*. Judging whether your "th" came out right needs phoneme-level assessment,
  which is a different class of tool.

This choice has a useful side effect. The known weakness of Whisper-style speech
recognition is that it returns more fluent text than what was said: it smooths over
hesitation and sometimes fixes grammar. For a grammar tutor that would be fatal — the
mistake disappears before anyone can correct it. For a vocabulary tutor it matters far
less: the recogniser rarely puts a word in your mouth that you did not say.

### 3. `claude -p` first, the API later

The same path the web adapter already uses: the `claude` binary, installed and logged
in, spending the Pro plan. And the same safety rule: **the child process gets no tools
at all.** In the text-anchored mode, a text fetched from the web goes into the tutor's
prompt, so a hostile page is a prompt-injection attempt aimed at a model with no
hands.

Two things to know before building on it:

- **Latency is the risk, and it has to be measured, not assumed.** The CLI costs
  seconds just to start, before the model writes a word. Whether a turn fits in a
  bearable time is the first thing to find out (see milestone S0).
- **The swap to the API should be cheap.** §14 of the MVP asks for one abstraction and
  no more, with a second real implementation to justify it. The tutor has exactly that
  situation — the CLI now, the API later — so it gets the same shape as `Adapter`: one
  protocol, a `ClaudeCliTutor` now, an `ApiTutor` when it comes. Nothing else gets
  abstracted.

Moving to the API is **pay per use, not the Pro plan.** That change is yours to make,
with the measured latency in hand.

### 4. Two modes, every level

**Discuss a text.** You pick a text from the Library and the tutor opens with a question
about it. It already has good material to work with: the adapted text, its glossary,
and the comprehension questions that come in every import. The session's target words
include the text's glossary, so you are practising out loud exactly the words you just
read.

**Free conversation.** You pick a topic, or let the tutor propose one. The target words
come from your deck: what is being learned, what is about to fall due, and the i+1
words of the next band.

**Every level from A1 to C2** — and a tutor at A1 and a tutor at C2 are not the same
tutor with a different number. The per-level rules already exist in `data/levels.toml`
(sentence length, allowed grammar, guidance) and are quoted into the adaptation prompt
today; the tutor reuses them rather than inventing a second definition of what "A2"
means.

| | A1–A2 | B1–B2 | C1–C2 |
|---|---|---|---|
| Tutor's sentences | Short, one clause, slow speech | Subordination allowed, normal pace | Anything, natural pace |
| Questions it asks | Concrete, often closed ("Do you like…?") | Open, "why" and "how" | Opinion, hypothesis, debate |
| What counts as a good answer | A word or a phrase is fine | A few connected sentences | An argument, with nuance and register |
| Target vocabulary | Everyday words of the band | Abstract and topic words | Precision, idiom, collocation |

---

## How the vocabulary loop works

**Before the session**, the app picks a small set of target words — between five and
eight, the same i+1 budget as an adapted text — and puts them in the tutor's prompt with
the instruction to create chances to use them. Not to ask for them by name.

**After every turn**, the app — not the model — lemmatises your transcript and checks
it against the targets. This is the kind of check that has to live in code: a model
asked "did the user use the word?" will sometimes say yes to be encouraging.

**One subtlety matters: an echo is not a use.** If the tutor said "enormous" and you
answer "yes, enormous", you repeated, you did not produce. A use counts as **unprompted**
only when the tutor has not said that word in the last few turns. Both are recorded; only
the unprompted one is evidence.

**When the tutor notices a better word** — you said "very big" where "huge" would do, or
dropped into Portuguese for one word — it says so briefly, in its reply. Those
suggestions become card candidates.

**At the end of the session**, a summary: the targets you used unprompted, the ones you
used after an echo, the ones you never reached, and the suggested words, each with a
save button. **Nothing is saved automatically.** The rule of the whole app holds here:
what you learn comes out of what you choose to save.

### A new, honest number for the dashboard

Today the ladder counts what you **recognise**. The tutor makes it possible to count what
you **produce**: words from the deck that you have used unprompted in speech. That could
become a second layer on the ladder — passive vocabulary and active vocabulary, per band.

With the same caution the dashboard already has about its other numbers: using a word
once in a session built to elicit it is weak evidence. The measure has to say how it was
earned, or it becomes the first number on that screen that flatters you.

---

## The technical pieces

| Piece | Choice | Why |
|---|---|---|
| Speech out (TTS) | The browser's `speechSynthesis` | Already planned for v2 — **V3 depends on it** |
| Speech in (STT) | Local `faster-whisper`, small model, CPU | Offline, free, audio never leaves the machine |
| The tutor | `claude -p`, no tools | The Pro plan, the same path as the web adapter |
| Level check | `coverage()` on every reply | The same code that measures imported texts |
| Vocabulary check | spaCy lemmas against the targets | Deterministic, as everything the app counts |

The alternative for speech in is the browser's Web Speech API, which is zero install —
but it only works properly in Chrome and Edge, and **it sends your audio to Google's
servers.** Local recognition is the choice that matches an app that runs on localhost
with no account anywhere.

### Privacy

A voice recording is more sensitive than a text. By default **no audio is stored** — only
the transcript. Keeping recordings, for listening back to yourself later, is an explicit
choice, off by default.

### Data, roughly

```
conversation   id, mode (text | free), level, text_id?, started_at, ended_at
turn           id, conversation_id, speaker (tutor | you), transcript,
               coverage_pct, created_at
word_use       word_id, turn_id, unprompted (bool)
```

Sketched, not designed. It is here to show that the tutor sits beside the deck instead
of changing it: `word` and `review` do not gain a column.

---

## Milestones

Each one usable on its own, with a done criterion you can check — the same shape as the
MVP's.

**S0 — The spike: measure before building.**
A throwaway script: one `claude -p` turn with a growing transcript of 2, 10 and 20
turns, and `faster-whisper` transcribing a 10-second clip on this machine.
*Done when:* the numbers are written down here, and they say whether a turn-based
conversation over `claude -p` is bearable.

**S1 — The conversation engine, typed.**
The tutor, the per-level behaviour, the reply measured with `coverage()` — with the
keyboard as input. Typing first separates "does the tutor behave?" from "does the
microphone work?".
*Done when:* a typed conversation about a text holds at A1 and at C1, and the replies
measure at their level.

**S2 — Voice in and voice out.**
Recording in the browser, `faster-whisper` on the server, `speechSynthesis` for the
reply, with the pace set by level.
*Done when:* you have a spoken ten-turn conversation without touching the keyboard.

**S3 — The vocabulary loop.**
Target words, the check after every turn, echo versus unprompted, suggested words, the
end-of-session summary with save buttons.
*Done when:* a session ends with a list of what you used, what you missed, and what you
can save — and the list is right.

**S4 — Free conversation.**
Topics, targets drawn from the deck instead of a text.
*Done when:* a free conversation at B1 steers you into at least half of its targets.

**S5 — Active vocabulary on the dashboard.**
*Done when:* the ladder shows passive and active per band, and says how active was
counted.

**Later:** real-time conversation, the API backend, pronunciation assessment, the phone.

---

## Out of the first version

- **Pronunciation scoring.** Needs phoneme-level assessment; a transcript cannot do it.
- **Real-time, interruptible conversation.** After turn-based works.
- **Explicit grammar correction.** Recasting only, as decided.
- **The phone.** Speaking practice wants a phone even more than reading does — and it
  inherits every reason in [v2](v2-ideas.md) why leaving localhost is a declared project.

---

## Open questions

| # | The question | Recommendation |
|---|---|---|
| 1 | A tutor reply measures off-level. Regenerate, or only flag it? | Flag every one, regenerate only below a hard floor. With `claude -p`, regenerating doubles the wait |
| 2 | May an A1 learner drop into Portuguese when stuck? | Yes, at A1–A2. The tutor answers in English and offers the word — that dropped word is the best card candidate there is |
| 3 | Should using a deck word correctly count as an FSRS review? | **No.** FSRS is calibrated on card reviews; feeding it another kind of evidence moves every schedule on a guess. Keep it as a separate measure |
| 4 | Keep audio recordings? | Off by default; an explicit opt-in |
| 5 | At what latency per turn do we move to the API? | Decide after S0, with the number in hand. A guess: past ~8 seconds, a conversation stops feeling like one |
| 6 | Continue the CLI session between turns, or resend the transcript each time? | Resend first: stateless, simple, testable. Continuing the session is an optimisation S0 can measure |

## Prerequisites

- **Audio from v2** (the browser's `speechSynthesis`). Both the tutor's voice and
  anything like shadowing are built on it.
- **Two weeks of real use of the current app**, as §12 of the MVP asks — which also means
  a deck with real words in it, and the tutor has nothing to work with without one.
