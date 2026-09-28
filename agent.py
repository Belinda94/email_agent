"""The agent loop.

The model cannot touch the inbox. It can only emit text, including a
structured request to run one of the tools described in tool_schemas.py.
This file is the harness around that: it sends the conversation to the
model, runs whatever tool the model asks for, feeds the result back, and
repeats until the model answers in plain language instead.
"""

import json
import sys
from datetime import date

import anthropic

from email_tools import get_email, list_labels, search_emails
from tool_schemas import TOOLS

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 1500

# A confused model can ask for tools forever. This is the circuit breaker.
MAX_ITERATIONS = 10

# Name to function. Looking up beats an if-chain: adding a tool means
# adding one line here and one schema, nothing else.
DISPATCH = {
    "search_emails": search_emails,
    "get_email": get_email,
    "list_labels": list_labels,
}

SYSTEM_PROMPT = """You are an assistant with read-only access to the user's \
Gmail inbox.

Today's date is {today}. The inbox contains over 13,000 messages, so always \
narrow your searches rather than fetching broadly.

How to work:
- Search before you act. You cannot know an email_id without finding it first.
- Resolve relative dates ("yesterday", "last week") into YYYY-MM-DD before \
calling a tool.
- Only set a filter the user actually asked for. An extra filter can exclude \
the very message they meant.
- If a request is ambiguous or you lack information you cannot look up, say \
so and ask, rather than guessing.
- You have read-only access. You cannot send, delete, archive or label \
anything. If asked to, say plainly that you cannot.

Answer in plain prose. Be concise."""


def run_tool(name, arguments):
    """Execute one tool call and return its result as a string.

    Errors are caught and returned as text rather than raised, so the model
    sees what went wrong and can correct itself on the next turn instead of
    the whole run crashing.
    """
    function = DISPATCH.get(name)
    if function is None:
        return f"Error: no tool named {name}"

    try:
        result = function(**arguments)
    except Exception as exc:
        return f"Error running {name}: {exc}"

    if result in ([], None, ""):
        return "No results."

    return json.dumps(result, indent=2, default=str)


def ask(question, verbose=True):
    """Answer one question about the inbox, using tools as needed."""
    client = anthropic.Anthropic()
    system = SYSTEM_PROMPT.format(today=date.today().isoformat())
    messages = [{"role": "user", "content": question}]

    for iteration in range(MAX_ITERATIONS):
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=system,
            tools=TOOLS,
            messages=messages,
        )

        # Not asking for a tool means it is answering. We are done.
        if response.stop_reason != "tool_use":
            return "".join(
                block.text for block in response.content
                if block.type == "text"
            )

        # The assistant's turn goes back verbatim, tool_use blocks included.
        messages.append({"role": "assistant", "content": response.content})

        # A single turn can request more than one tool. Every tool_use block
        # must get a matching tool_result, or the next call is rejected.
        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue

            if verbose:
                args = ", ".join(f"{k}={v!r}" for k, v in block.input.items())
                print(f"  -> {block.name}({args})")

            results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": run_tool(block.name, block.input),
            })

        messages.append({"role": "user", "content": results})

    return (
        f"Stopped after {MAX_ITERATIONS} tool calls without reaching an "
        "answer. The question may be too broad, or a tool description may "
        "be steering the model in circles."
    )


def main():
    # A question on the command line runs once; otherwise go interactive.
    if len(sys.argv) > 1:
        print(ask(" ".join(sys.argv[1:])))
        return

    print("Ask about your inbox. Blank line or Ctrl-C to quit.\n")
    while True:
        try:
            question = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return

        if not question:
            return

        print(ask(question))
        print()


if __name__ == "__main__":
    main()
