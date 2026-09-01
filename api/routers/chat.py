import json
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from rag.engine import ask, describe_event, stream_events
from api.schemas import ChatIn, ChatOut

router = APIRouter()

@router.post("/", response_model=ChatOut)
def post_chat(body: ChatIn):
    try:
        result = ask(body.question)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"The RAG engine failed unexpectedly: {exc}") from exc
    return ChatOut(answer=result.answer, sql=result.sql, has_sql=result.sql is not None)

@router.post("/stream")
def post_chat_stream(body: ChatIn):
    """Same question-answering as POST /, streamed as Server-Sent Events
    instead of one final JSON body - see README.md ("Chat endpoint (RAG)")
    for the event shapes and why this exists alongside the plain endpoint
    rather than replacing it."""
    def event_source():
        # Not returning early after the "result" event: letting this for
        # loop run one more iteration lets stream_events() hit its own
        # internal `return` and close its `with open_connection(...)`
        # block deterministically, rather than abandoning it mid-suspension
        # and relying on garbage collection to close it eventually.
        try:
            for event in stream_events(body.question):
                if event["type"] == "result":
                    result = event["result"]
                    payload = {"answer": result.answer, "sql": result.sql, "has_sql": result.sql is not None}
                    yield f"event: result\ndata: {json.dumps(payload)}\n\n"
                    continue
                message = describe_event(event)
                if message is not None:
                    payload = {"turn": event.get("turn"), "message": message}
                    yield f"event: progress\ndata: {json.dumps(payload)}\n\n"
        except Exception as exc:
            yield f"event: error\ndata: {json.dumps({'detail': str(exc)})}\n\n"

    return StreamingResponse(event_source(), media_type="text/event-stream")
