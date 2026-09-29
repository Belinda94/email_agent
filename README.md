# Read-only email agent

A command-line agent that answers questions about a Gmail inbox. It reads;
it cannot send, delete, archive or label anything. That constraint is
deliberate and is discussed below.

Built to understand agent tool-use from the inside: how a model decides
which tool to call, how tool descriptions steer that decision, and what
breaks when the tools underneath are subtly wrong.

```
$ python agent.py "did I ever reply to Abojani?"
  -> search_emails(sender='Abojani')
  -> search_emails(recipient='Abojani')
  -> get_email(email_id='8773')
  -> get_email(email_id='8081')

Yes. The thread under "INQUIRY ON ONLINE FINANCIAL LITERACY PROGRAM":
you wrote first in April 2024, they replied with an attachment, you
followed up in September asking about an upcoming class, and your last
message acknowledged receipt.
```

The `->` lines are the tools the model chose. It searched by sender and
by recipient separately, because those answer different halves of the
question.

## How it works

The model cannot touch the inbox. All it can do is produce text, including
a structured request to run one of the tools it has been given. Everything
else is ordinary Python.

1. The user's question plus the tool schemas go to the Claude API.
2. If the response asks for a tool, the harness runs the matching Python
   function.
3. The result is appended to the conversation and sent back.
4. This repeats until the model answers in plain language instead of
   asking for another tool.

An iteration cap stops a confused model looping forever. Tool errors are
returned to the model as text rather than raised, so it can correct its
own call on the next turn instead of the run dying.

| File | Purpose |
| --- | --- |
| `email_tools.py` | IMAP access: search, fetch, list labels |
| `tool_schemas.py` | The JSON schemas the model actually reads |
| `agent.py` | The loop, and the dispatch from tool name to function |

## Why read-only

Writes are not a missing feature, they are a deferred design problem.

The inbox this was built against holds over 13,000 messages. Letting a
model pick deletion targets from a text query across that, with no undo,
is not a risk worth taking for convenience. Archiving is the safer version
of the same intent and is planned for v2 behind an explicit confirmation
step: the model proposes, the user approves, then it acts.

Building that gate properly is more interesting than bolting the tools on,
so it is a separate piece of work rather than a rushed addition here.

## Schema design

The model never sees `email_tools.py`. It sees the descriptions in
`tool_schemas.py`, so those descriptions are the entire interface. Three
of them exist to prevent specific failures:

**`unread_only` warns against a substitution.** Asked "what have I not
replied to", a model will reach for the nearest available filter, and
unread is the nearest. But read and replied-to are different things, and a
message can be read and still unanswered. There is no parameter that can
filter on "did I reply" - the honest answer is to retrieve candidates and
inspect them, so the description says so.

**`get_email` forbids inventing an id.** A fabricated id either errors or
silently returns the wrong message, and the second is worse. The
description states that ids must come from a previous search.

**`get_email` closes off attachments.** Filenames are listed; contents
cannot be retrieved. Without saying so, a model will offer to summarise an
attachment it has no way to read.

`list_labels` exists for the same reason: so the model can check which
labels are real instead of guessing a plausible name.

## What broke

Three things surfaced from running real questions against a real inbox.

**Sent mail was invisible.** Searches were scoped to INBOX, where sent mail
does not live, so any question about what the user had sent returned
nothing. Fixed by widening the scope when the query is about a recipient.

**Ids resolved to the wrong messages.** This one returned confidently
wrong data rather than erroring, which makes it the worst of the three.

> Emails live on a server, and IMAP lets a client read them. Messages are
> grouped into mailboxes - Inbox, Sent Mail, All Mail - and each message
> has a position number within its mailbox. Position 5547 in All Mail and
> position 5547 in Inbox are different emails, and positions shift as mail
> arrives. Search looked in All Mail and returned position 5547; the fetch
> then looked in Inbox and returned something else entirely.

The fix was to work in a single mailbox and address messages by UID, which
is a permanent identifier rather than a position. Inbox scope became a
search term (`in:inbox`) instead of a choice of mailbox.

It surfaced because the agent returned a newsletter in answer to a question
about a sent email - the mismatch was visible in the output. A crash
announces itself; a wrong answer does not.

**One investigation found nothing wrong.** A search for mail sent to an
organisation returned no results, which looked like a bug. Checking the
actual data showed the organisation's name appeared in subjects and bodies
but never in a recipient address, so a header search correctly matched
nothing. The agent had said as much - that it found nothing and the address
might differ - and it was right. Worth recording, because the instinct to
fix working code is as costly as missing a real bug.

## Setup

Requires Python 3.10+, a Gmail account with 2-factor authentication, and an
Anthropic API key.

```bash
git clone <this repo>
cd email-agent
python3 -m venv .venv && source .venv/bin/activate
pip install anthropic python-dotenv fastapi uvicorn
```

Create a Gmail app password at `myaccount.google.com/apppasswords`, then a
`.env` file:

```
GMAIL_ADDRESS=you@gmail.com
GMAIL_APP_PASSWORD=your16charpassword
ANTHROPIC_API_KEY=sk-ant-...
```

`.env` is gitignored and should stay that way.

```bash
python agent.py "what's new in my inbox?"   # one question
python agent.py                              # interactive
uvicorn web:app --reload                     # browser, at 127.0.0.1:8000
```

The browser interface shows the tool calls above each answer, which the
command line also prints. That trace is the point: it is the difference
between an agent choosing its own tools and a chatbot guessing.

`python diagnose.py` lists the mailboxes your account exposes over IMAP,
which is useful if folder names differ from the defaults.

## Limitations

- Gmail-specific. `X-GM-RAW` and `X-GM-LABELS` are Google IMAP extensions.
- HTML email is stripped with regex rather than a parser. Crude, but it
  avoids a dependency for something that only needs to recover the words.
- Bodies are truncated at 4,000 characters to keep a long newsletter from
  flooding the model's context.
- Senders whose plain-text part just says "your client cannot display
  HTML" will return exactly that. A known cost of preferring plain text.
- Searching all mail is slower than searching the inbox, which is why the
  result limit is capped.

## What I would do differently

**Get usable content out of every email, not just the well-behaved ones.**
The tool prefers the plain-text part of a message and falls back to HTML.
That sounds sensible until you meet a sender whose plain-text part reads
"your email software can't display HTML emails, click here" - which is
technically plain text and completely useless. A better rule would compare
what each part actually yields and pick the one with real content, rather
than trusting the format label.

**Question the body truncation, not just the number.** Bodies are cut at
4,000 characters. I picked that figure without measuring anything. The
better question is whether the full body is needed at all once a summary
exists - most questions are answered by subject, sender and the first few
lines, and fetching more is wasted context. Truncation is a guess standing
in for a decision I have not made yet.

**Searches cannot reach Spam or Trash.** Everything runs against All Mail,
which by design excludes both. But "did that email go to spam?" and
"did I delete it?" are ordinary questions, and right now the agent answers
them wrongly - it reports finding nothing, which reads as "it doesn't
exist" rather than "I didn't look there." A correct version would either
search those folders too or say explicitly which folders it did not check.
That second part matters more than the first: a tool that quietly omits a
whole category of mail is the same class of problem as the UID bug, where
the answer looked fine and wasn't.

**Automatic categorisation, once writes exist.** The feature I actually
want is a tool that reads unfiled mail, proposes labels, and files it -
years of marketing mail from companies I no longer recognise, sorted
without me reading each one. It is not here because it needs exactly what
this version deliberately does not have: the ability to create labels and
apply them. Building it means building the confirmation gate first, and
deciding what the model may do unsupervised versus what needs approval.
That is the whole of v2, not an addition to v1.
