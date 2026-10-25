"""Local, single-worker API; graph checkpoints include source snapshots."""

import secrets
import threading
import uuid
from contextlib import asynccontextmanager

import requests
from fastapi import Depends, FastAPI, Header, HTTPException
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from core.config import settings
from schemas import ApprovalRequest, ReviewRequest, ReviewResponse
from workflow import build_graph


def authorize(authorization: str | None = Header(default=None)):
    if settings.api_token and not secrets.compare_digest(
        authorization or "", "Bearer " + settings.api_token.get_secret_value()
    ):
        raise HTTPException(status_code=401, detail="Valid bearer token required")


def create_app(graph=None):
    lock = threading.Lock()

    @asynccontextmanager
    async def lifespan(app):
        if settings.enable_github_posting and (not settings.api_token or not settings.github_token):
            raise RuntimeError("Posting requires API_TOKEN and GITHUB_TOKEN")
        if graph is not None:
            app.state.graph = graph
            yield
        else:
            settings.data_dir.mkdir(parents=True, exist_ok=True)
            with SqliteSaver.from_conn_string(
                str(settings.data_dir / "checkpoints.sqlite")
            ) as saver:
                app.state.graph = build_graph(saver)
                yield

    app = FastAPI(title="Code Reviewer", lifespan=lifespan)

    def config(review_id):
        return {
            "configurable": {"thread_id": review_id},
            "recursion_limit": 20,
            "metadata": {"review_id": review_id},
        }

    def snapshot(review_id):
        try:
            uuid.UUID(review_id)
        except ValueError as exc:
            raise HTTPException(404, "Review not found") from exc
        state = app.state.graph.get_state(config(review_id))
        if not state.values:
            raise HTTPException(404, "Review not found")
        return state

    def response(review_id):
        snap = snapshot(review_id)
        values = dict(snap.values)
        if any(task.error for task in snap.tasks):
            values["status"] = "failed"
        # Never return raw source snapshots through the status endpoint.
        values["context"] = [
            {k: v for k, v in item.items() if k != "content"} for item in values.get("context", [])
        ]
        return ReviewResponse.model_validate(values)

    def invoke(payload, review_id):
        try:
            app.state.graph.invoke(payload, config(review_id))
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 502
            code = 404 if status == 404 else 429 if status == 429 else 502
            raise HTTPException(
                code, {"message": "GitHub request failed", "review_id": review_id}
            ) from exc
        except Exception as exc:
            raise HTTPException(
                502,
                {
                    "message": "Review step failed; inspect status and resume",
                    "review_id": review_id,
                },
            ) from exc
        return response(review_id)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/review", response_model=ReviewResponse, dependencies=[Depends(authorize)])
    def review(request: ReviewRequest):
        review_id = str(uuid.uuid4())
        with lock:
            return invoke(
                {
                    "review_id": review_id,
                    "request": request.model_dump(),
                    "status": "fetching",
                    "candidates": [],
                    "errors": [],
                    "metrics": {},
                },
                review_id,
            )

    @app.get(
        "/reviews/{review_id}", response_model=ReviewResponse, dependencies=[Depends(authorize)]
    )
    def status(review_id: str):
        with lock:
            return response(review_id)

    @app.post(
        "/reviews/{review_id}/approval",
        response_model=ReviewResponse,
        dependencies=[Depends(authorize)],
    )
    def approve(review_id: str, decision: ApprovalRequest):
        with lock:
            snap = snapshot(review_id)
            if not any(task.interrupts for task in snap.tasks):
                raise HTTPException(409, "Review is not awaiting approval")
            return invoke(Command(resume=decision.model_dump()), review_id)

    @app.post(
        "/reviews/{review_id}/resume",
        response_model=ReviewResponse,
        dependencies=[Depends(authorize)],
    )
    def resume(review_id: str):
        with lock:
            snap = snapshot(review_id)
            if not snap.next or any(task.interrupts for task in snap.tasks):
                raise HTTPException(409, "Only failed, non-interrupted reviews can be resumed here")
            return invoke(None, review_id)

    return app


app = create_app()


def main():
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000, workers=1)


if __name__ == "__main__":
    main()
