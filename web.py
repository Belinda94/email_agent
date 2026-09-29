"""A small web interface for the email agent.

The agent loop is unchanged - this imports it and adds a browser front end.
The point of the page is the tool trace: showing which tools the model chose
and what arguments it passed is what distinguishes an agent from a chatbot.
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

from agent import ask

app = FastAPI(title="Email Agent")

HERE = Path(__file__).parent


class Question(BaseModel):
    question: str


@app.get("/")
def index():
    return FileResponse(HERE / "static" / "agent_ui.html")


# A plain def, not async def: the IMAP and API calls block, and FastAPI runs
# sync endpoints in a threadpool so one slow question does not freeze the app.
@app.post("/ask")
def handle(payload: Question):
    trace = []

    def record(name, arguments):
        args = ", ".join(f"{k}={v!r}" for k, v in arguments.items())
        trace.append(f"{name}({args})")

    try:
        answer = ask(payload.question, on_tool_call=record)
    except Exception as exc:
        return {"trace": trace, "answer": f"Something went wrong: {exc}"}

    return {"trace": trace, "answer": answer}
