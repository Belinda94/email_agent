# AI email agent

A command-line agent that answers questions about a Gmail inbox, and a
separate approval step that files bulk mail out of it.

The split is the design. The agent reads and describes; it cannot send,
delete, archive or label anything. Writes happen in a script the user runs,
where every batch is previewed and approved by hand. The reasons are below.

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

The `->` lines are the tools the model chose. It searched by sender and by
recipient separately, because those answer different halves of the question.

## How it works

The model cannot touch the inbox. All it can do is produce text, including
a structured request to run one of the tools it has been given. Everything
else is ordinary Python.

1. The user's question plus the tool schemas go to the Claude API.
2. If the response asks for a tool, the harness runs the matching Python
   function.
3. The result is appended to the conversation and sent back.
4. This repeats until the model answers in plain language instead of asking
   for another tool.

An iteration cap stops a confused model looping forever. Tool errors are
returned to the model as text rather than raised, so it can correct its own
call on the next turn instead of the run dying.

| File | Purpose |
| --- | --- |
| `email_tools.py` | IMAP access: search, fetch, summarise, label, archive |
| `tool_schemas.py` | The JSON schemas the model actually reads |
| `agent.py` | The loop, and the dispatch from tool name to function |
| `web.py`, `static/` | Browser interface showing the tool trace |
| `categorise.py` | Names the categories for one sender's subject patterns |
| `categorise_all.py` | Scans the whole inbox and caches the breakdown |
| `clear_bulk.py` | The approval loop: preview, approve, label, archive |
| `consolidate.py` | Folds old sub-labels into one Bulk label |
| `diagnose.py` | Lists the mailboxes an account exposes over IMAP |

## Finding what is actually in an inbox

The inbox this was built against holds over 13,000 messages. Asking a model
to read them and report back is slow, expensive, and pours message bodies
into a prompt for no good reason. The work happens in three stages, and only
the last one involves the model.

**Group by sender.** One IMAP pass fetches the From and Subject headers for
every message and counts by address. Addresses that share a root domain are
flagged as related, because a sender split across two subdomains would
otherwise look like two senders, and clearing one would silently leave the
other behind.

**Collapse repeated subjects.** Bulk senders reuse a handful of templates
with the variable parts swapped in. Blanking out dates, amounts and
reference numbers makes near-identical subjects collapse into one group. In
practice this takes a sender from 541 messages to 22 patterns, mechanically,
with no model involved.

**Name the groups.** 22 rows is small enough to hand to the model in one
call. Promotional subjects differ on purpose and a bank labels the same
statement three different ways, so the final grouping needs meaning rather
than string matching. The model sees the pattern list only - never a message
body - and returns five or six named categories.

The result caches to disk. That is not only a speed trick: the approval step
has to act on exactly the categories that were shown, and recomputing them
would let the grouping drift between preview and action.

### Telling junk from mail that matters

The categoriser is told who the user is before it is shown anything. Without
that line, every automated message reads as bulk, and job alerts - the most
relevant mail in this particular inbox - were marked as clutter on the first
run. Relevance is not a property of an email. It is a property of an email
and a person.

## Why the agent cannot write

Writes are not a missing feature. They run through a different path on
purpose.

A model cannot be relied on to ask permission, so the permission step sits
outside it. The agent can report that 82 messages from one sender look like
promotions; it has no tool that would let it act on that. Filing happens in
`clear_bulk.py`, which shows each batch with its counts and real example
subjects, and waits for a yes.

Approval is also batched rather than per-message, because a gate that asks
800 times is a gate nobody uses. The unit being approved is a category - one
sender, one kind of mail - which is small enough to judge and large enough to
be worth approving.

**Nothing is ever deleted.** Archiving removes the `\Inbox` label and leaves
the message in All Mail, searchable as before. Every archived batch also gets
a `Bulk` label, so the whole lot can be opened in Gmail, looked at, and
deleted there in one action if the user wants. Deletion stays in the
interface where a person can see what they are removing.

## Schema design

The model never sees `email_tools.py`. It sees the descriptions in
`tool_schemas.py`, so those descriptions are the entire interface. Several
exist to prevent specific failures:

**`unread_only` warns against a substitution.** Asked "what have I not
replied to", a model will reach for the nearest available filter, and unread
is the nearest. But read and replied-to are different things, and a message
can be read and still unanswered. There is no parameter that can filter on
"did I reply" - the honest answer is to retrieve candidates and inspect them,
so the description says so.

**`get_email` forbids inventing an id.** A fabricated id either errors or
silently returns the wrong message, and the second is worse.

**`get_email` closes off attachments.** Filenames are listed; contents cannot
be retrieved. Without saying so, a model will offer to summarise an
attachment it has no way to read.

**`summarise_senders` says when not to use `search_emails`.** Asked who fills
the inbox, search returns twenty subject lines from one sender. The
description points at the counting tool instead.

**`read_categories` says what to do when it returns nothing.** The cache is
built by a script the agent cannot run, so the description tells it to say so
rather than improvising.

`list_labels` exists for the same family of reasons: so the model can check
which labels are real instead of guessing a plausible name.

## What broke

**Sent mail was invisible.** Searches were scoped to INBOX, where sent mail
does not live, so any question about what the user had sent returned nothing.

**Ids resolved to the wrong messages.** This one returned confidently wrong
data rather than erroring, which makes it the worst of them.

> Emails live on a server, and IMAP lets a client read them. Messages are
> grouped into mailboxes - Inbox, Sent Mail, All Mail - and each message has
> a position number within its mailbox. Position 5547 in All Mail and
> position 5547 in Inbox are different emails, and positions shift as mail
> arrives. Search looked in All Mail and returned position 5547; the fetch
> then looked in Inbox and returned something else entirely.

The fix was to work in a single mailbox and address messages by UID, which is
a permanent identifier rather than a position. Inbox scope became a search
term (`in:inbox`) instead of a choice of mailbox.

It surfaced because the agent returned a newsletter in answer to a question
about a sent email - the mismatch was visible in the output. A crash
announces itself; a wrong answer does not.

**One investigation found nothing wrong.** A search for mail sent to an
organisation returned no results, which looked like a bug. Checking the data
showed the organisation's name appeared in subjects and bodies but never in a
recipient address, so a header search correctly matched nothing. The agent
had said as much - that it found nothing and the address might differ - and
it was right. Worth recording, because the instinct to fix working code is as
costly as missing a real bug.

**Two failures in the write path, both caught by the same habit.** An escaped
backslash made the un-archive command unparseable, and a sender name with an
em dash broke label creation, because IMAP encodes mailbox names as ASCII.
Both failed loudly and neither was logged as successful, so re-running after
the fix picked up exactly where it stopped. Recording only confirmed writes is
what made that safe.

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

`.env` is gitignored and should stay that way, along with the two JSON files
the scripts write - they hold senders and subject lines.

```bash
python agent.py "what's new in my inbox?"   # one question
python agent.py                              # interactive
uvicorn web:app --reload                     # browser, at 127.0.0.1:8000
```

To file bulk mail, build the breakdown first, then approve it:

```bash
python categorise_all.py build   # one scan, one API call per large sender
python categorise_all.py bulk    # what could be cleared
python clear_bulk.py             # approve batch by batch
```

Edit `DEFAULT_CONTEXT` in `categorise.py` before the first build. It is the
only thing telling the categoriser what matters to you.

`python diagnose.py` lists the mailboxes your account exposes over IMAP,
which is useful if folder names differ from the defaults.

## Limitations

- Gmail-specific. `X-GM-RAW` and `X-GM-LABELS` are Google IMAP extensions.
- HTML email is stripped with regex rather than a parser. Crude, but it
  avoids a dependency for something that only needs to recover the words.
- Bodies are truncated at 4,000 characters.
- Senders whose plain-text part just says "your client cannot display HTML"
  will return exactly that. A known cost of preferring plain text.
- The scan has a ceiling of 2,000 messages, newest first. It says when it
  hits it, but most of a large mailbox goes unexamined by default.
- Searches cannot reach Spam or Trash, and do not say so.
- Labels are ASCII only.

## What I would do differently

**Get usable content out of every email, not just the well-behaved ones.**
The tool prefers the plain-text part and falls back to HTML. That sounds
sensible until you meet a sender whose plain-text part reads "your email
software can't display HTML emails, click here" - which is technically plain
text and completely useless. A better rule would compare what each part
actually yields rather than trusting the format label.

**Question the body truncation, not just the number.** I picked 4,000
characters without measuring anything. The better question is whether the
full body is needed at all once a summary exists. Truncation is a guess
standing in for a decision I have not made yet.

**Searches cannot reach Spam or Trash.** Everything runs against All Mail,
which by design excludes both. But "did that email go to spam?" is an
ordinary question, and right now the agent answers it wrongly - it reports
finding nothing, which reads as "it doesn't exist" rather than "I didn't look
there". A correct version would either search those folders or say which ones
it skipped. That second part matters more: a tool that quietly omits a whole
category of mail is the same class of problem as the UID bug, where the
answer looked fine and wasn't.

**Approval belongs in the browser, not the terminal.** The web interface
already exists and would show the full preview rather than three truncated
patterns. What it must not become is approval through the chat: if the model
interprets "yes, archive those", it is back in control of the write, which is
the one thing this design is built to prevent.
