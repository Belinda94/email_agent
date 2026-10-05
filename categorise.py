"""Group subject patterns into categories.

summarise_subjects does the mechanical half: it blanks out dates, amounts
and reference numbers so repeated templates collapse. That takes a sender
from hundreds of messages down to a few dozen rows.

It cannot go further. Promotional subjects differ on purpose, and a bank
labels the same monthly statement three different ways. Grouping those
needs meaning, not string matching - which is what this does, by handing
the model the short list rather than the full mailbox.

The model never sees the emails. It sees the pattern list, which is the
whole point: cheap, fast, and it cannot leak message bodies into a prompt.
"""

import json

import anthropic

MODEL = "claude-sonnet-4-6"

PROMPT = """You are sorting one person's email. Here is who they are:

{context}

Below are subject-line patterns from a single sender, with how many \
messages match each. Group them into categories by what the mail actually \
is.

Rules:
- Use at most {max_categories} categories. Fewer is better. If the \
distinctions are not ones this person would act on differently, merge them.
- Name each category in plain words a person would use.
- Every pattern must appear in exactly one category.
- needs_attention means: this person would want this where they can see it. \
Set it true for anything they must act on, any record they may need later \
(statements, receipts, security codes, confirmations), and anything that \
bears on what they are currently working towards, as described above. Set \
it false only for mail that is purely promotional or announcement-like for \
this particular person.
- Do not recommend deleting or archiving anything. Only describe.

Patterns:
{patterns}

Reply with JSON only, no preamble, no markdown fences:
{{"categories": [{{"name": "...", "needs_attention": true, \
"pattern_indexes": [0, 2, 5]}}]}}"""


# Edit this to match what currently matters to you. It is the only thing
# telling the model that a job alert is not junk - without it, anything
# automated reads as bulk.
DEFAULT_CONTEXT = """A data analyst in Kenya, actively applying for remote \
and freelance work in data and AI. Mail about job openings, applications, \
interviews, assessments and platform onboarding matters to them, even when \
it is automated or sent in bulk. They also study part-time, run small \
businesses, and manage their own banking and finances."""


def category_budget(message_count):
    """Cap how finely a sender can be split, based on how much mail it sends.

    Fourteen messages carved into five categories is noise: the split has to
    be one the person would act on differently, and at that size it is not.
    """
    if message_count < 20:
        return 2
    if message_count < 60:
        return 4
    return 6


def categorise_patterns(patterns, model=MODEL, context=None,
                        max_categories=None):
    """Turn a list of subject patterns into named categories.

    patterns is the list from summarise_subjects: dicts with count and
    example. Returns the same categories with counts totalled, or a single
    fallback category if the model's reply cannot be parsed - a failure
    here should degrade to "uncategorised", never crash the caller.
    """
    if not patterns:
        return []

    listing = "\n".join(
        f"{i}. ({p['count']}x) {p['example']}"
        for i, p in enumerate(patterns)
    )

    total = sum(p["count"] for p in patterns)
    if max_categories is None:
        max_categories = category_budget(total)

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=model,
        max_tokens=1000,
        messages=[{"role": "user", "content": PROMPT.format(
            context=context or DEFAULT_CONTEXT,
            patterns=listing,
            max_categories=max_categories,
        )}],
    )

    text = "".join(b.text for b in response.content if b.type == "text")
    text = text.strip().removeprefix("```json").removeprefix("```")
    text = text.removesuffix("```").strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return [{
            "name": "Uncategorised",
            "needs_attention": True,
            "count": sum(p["count"] for p in patterns),
            "patterns": patterns,
        }]

    categories = []
    claimed = set()

    for category in parsed.get("categories", []):
        indexes = [i for i in category.get("pattern_indexes", [])
                   if isinstance(i, int) and 0 <= i < len(patterns)]
        members = [patterns[i] for i in indexes]
        claimed.update(indexes)
        if not members:
            continue
        categories.append({
            "name": category.get("name", "Unnamed"),
            "needs_attention": bool(category.get("needs_attention", True)),
            "count": sum(p["count"] for p in members),
            "patterns": members,
        })

    # Anything the model skipped is kept, flagged for attention. Silently
    # dropping patterns would make the totals lie, and a preview that
    # undercounts is worse than one that is untidy.
    missed = [p for i, p in enumerate(patterns) if i not in claimed]
    if missed:
        categories.append({
            "name": "Not categorised",
            "needs_attention": True,
            "count": sum(p["count"] for p in missed),
            "patterns": missed,
        })

    categories.sort(key=lambda c: c["count"], reverse=True)
    return categories


if __name__ == "__main__":
    import sys

    from email_tools import summarise_subjects

    who = sys.argv[1] if len(sys.argv) > 1 else "noreply@ecobank.com"

    result = summarise_subjects(sender=who)
    print(f"{who}: {result['total']} messages, "
          f"{len(result['patterns'])} patterns")
    print("Grouping...")
    print()

    for category in categorise_patterns(result["patterns"]):
        flag = "KEEP?" if category["needs_attention"] else "bulk "
        print(f"[{flag}] {category['name']} - {category['count']} messages")
        for p in category["patterns"]:
            print(f"           {p['count']:>4}  {p['example'][:62]}")
        print()
