"""ClearCut web UI: upload a screenplay -> run the ADK pipeline -> render the clearance report.

Backend selection:
  * AGENT_ENGINE_ID set   -> calls the agent deployed on Vertex AI Agent Engine (production).
  * AGENT_ENGINE_ID empty -> runs the same ADK root_agent in-process (local development).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import markdown
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.requests import Request

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env", override=False)

PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "")
LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
AGENT_ENGINE_ID = os.getenv("AGENT_ENGINE_ID", "").strip()
MODEL = os.getenv("MODEL", "")
SAMPLE_PATH = ROOT / "samples" / "the_last_shift.fountain"
MAX_SCRIPT_CHARS = int(os.getenv("CLEARCUT_MAX_SCRIPT_CHARS", "400000"))

app = FastAPI(title="ClearCut", version="1.0.0")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

STAGES = ["extractor", "researcher", "adjudicator", "reporter"]
STAGE_LABELS = {
    "extractor": "Extracting clearable entities (Gemini)",
    "researcher": "Researching entities via Parallel Search",
    "adjudicator": "Adjudicating risk against evidence (Gemini)",
    "reporter": "Writing clearance report (Gemini)",
}

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def _loads(raw: Any, key: str) -> list[dict]:
    """Parse a JSON string / dict / list from agent state into a list of dicts."""
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = _FENCE.sub("", raw.strip())
        if not raw:
            return []
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if isinstance(raw, dict):
        raw = raw.get(key, [])
    return [x for x in raw if isinstance(x, dict)] if isinstance(raw, list) else []


# --------------------------------------------------------------------------- jobs

class Job:
    def __init__(self, script: str):
        self.id = uuid.uuid4().hex[:12]
        self.script = script
        self.started = time.time()
        self.finished: float | None = None
        self.stage: str | None = STAGES[0]  # the extractor starts immediately
        self.stages_done: list[str] = []
        self.entity_count: int | None = None
        self.researched: int | None = None
        self.search_errors: int | None = None
        self.entities: list[dict] = []
        self.findings: list[dict] = []
        self.report_md: str = ""
        self.error: str | None = None
        self.backend = "agent_engine" if AGENT_ENGINE_ID else "local"

    # ---- event handling shared by both backends
    def handle_event(self, ev: dict) -> None:
        author = ev.get("author")
        if author in STAGES and self.stage != author:
            if self.stage and self.stage not in self.stages_done:
                self.stages_done.append(self.stage)
            self.stage = author

        parts = ((ev.get("content") or {}).get("parts") or [])
        for p in parts:
            fr = p.get("function_response") or p.get("functionResponse")
            if fr and (fr.get("name") == "research_entities"):
                resp = fr.get("response") or {}
                if isinstance(resp, dict):
                    resp = resp.get("result", resp)
                if isinstance(resp, dict):
                    self.researched = resp.get("researched")
                    self.search_errors = resp.get("search_errors")

        delta = ((ev.get("actions") or {}).get("state_delta") or (ev.get("actions") or {}).get("stateDelta") or {})
        if "entities" in delta:
            ents = _loads(delta["entities"], "entities")
            if ents:
                self.entities = ents
                self.entity_count = len(ents)
        if "findings" in delta:
            f = _loads(delta["findings"], "findings")
            if f:
                self.findings = f
        if "report" in delta and isinstance(delta["report"], str) and delta["report"].strip():
            self.report_md = _FENCE.sub("", delta["report"].strip())

    def finish(self, error: str | None = None) -> None:
        if self.stage and self.stage not in self.stages_done:
            self.stages_done.append(self.stage)
        self.error = error
        self.finished = time.time()

    # ---- serialisation
    def summary(self) -> dict:
        counts = {"RED": 0, "AMBER": 0, "GREEN": 0}
        for f in self.findings:
            r = str(f.get("risk", "")).upper()
            if r in counts:
                counts[r] += 1
        return counts

    def status(self) -> dict:
        done = self.finished is not None
        return {
            "id": self.id,
            "backend": self.backend,
            "model": MODEL,
            "stage": self.stage,
            "stage_label": STAGE_LABELS.get(self.stage or "", ""),
            "stages": STAGES,
            "stages_done": self.stages_done,
            "entity_count": self.entity_count,
            "researched": self.researched,
            "search_errors": self.search_errors,
            "done": done,
            "error": self.error,
            "elapsed": round((self.finished or time.time()) - self.started, 1),
            "counts": self.summary() if done else None,
            "report_html": _md_to_html(self.report_md) if done and self.report_md else "",
            "findings": self.findings if done else [],
            "entities": self.entities if done else [],
        }

    def as_json(self) -> dict:
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "model": MODEL,
            "backend": self.backend,
            "counts": self.summary(),
            "entities": self.entities,
            "findings": self.findings,
            "report_markdown": self.report_md,
        }


JOBS: dict[str, Job] = {}


def _md_to_html(md: str) -> str:
    return markdown.markdown(md, extensions=["tables", "sane_lists"])


# --------------------------------------------------------------------------- backends

def _agent_engine_events(script: str, user_id: str) -> Iterator[dict]:
    import vertexai
    from vertexai import agent_engines

    vertexai.init(project=PROJECT, location=LOCATION)
    name = AGENT_ENGINE_ID
    if not name.startswith("projects/"):
        name = f"projects/{PROJECT}/locations/{LOCATION}/reasoningEngines/{AGENT_ENGINE_ID}"
    remote = agent_engines.get(name)
    session = remote.create_session(user_id=user_id)
    session_id = session["id"] if isinstance(session, dict) else getattr(session, "id", None)
    for ev in remote.stream_query(user_id=user_id, session_id=session_id, message=script):
        yield ev if isinstance(ev, dict) else json.loads(json.dumps(ev, default=str))


def _local_events(script: str, user_id: str) -> Iterator[dict]:
    """Run the ADK root_agent in-process and yield events as they happen (dev mode)."""
    import queue

    from google.adk.runners import InMemoryRunner
    from google.genai import types

    from clearcut_agent.agent import root_agent

    q: "queue.Queue[dict | None]" = queue.Queue()
    _END = None

    async def _run() -> None:
        runner = InMemoryRunner(agent=root_agent, app_name="clearcut")
        session = await runner.session_service.create_session(app_name="clearcut", user_id=user_id)
        async for ev in runner.run_async(
            user_id=user_id,
            session_id=session.id,
            new_message=types.Content(role="user", parts=[types.Part(text=script)]),
        ):
            q.put(ev.model_dump(mode="json", exclude_none=True))

    def _worker() -> None:
        try:
            asyncio.run(_run())
        except Exception as e:  # propagate to the consumer thread
            q.put({"_error": f"{type(e).__name__}: {e}"})
        finally:
            q.put(_END)

    threading.Thread(target=_worker, daemon=True).start()
    while True:
        ev = q.get()
        if ev is _END:
            return
        if "_error" in ev:
            raise RuntimeError(ev["_error"])
        yield ev


def _run_job(job: Job) -> None:
    try:
        user_id = f"web-{job.id}"
        events = _agent_engine_events(job.script, user_id) if AGENT_ENGINE_ID else _local_events(job.script, user_id)
        for ev in events:
            job.handle_event(ev)
        if not job.report_md:
            raise RuntimeError("Pipeline finished without producing a report.")
        job.finish()
    except Exception as e:  # surface the failure in the UI instead of hanging
        job.finish(error=f"{type(e).__name__}: {e}"[:600])


# --------------------------------------------------------------------------- routes

class ClearRequest(BaseModel):
    script: str


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {"model": MODEL, "backend": "Vertex AI Agent Engine" if AGENT_ENGINE_ID else "local ADK runner"},
    )


@app.get("/sample", response_class=PlainTextResponse)
def sample():
    return SAMPLE_PATH.read_text(encoding="utf-8")


@app.post("/clear")
def clear(req: ClearRequest):
    script = req.script.strip()
    if len(script) < 50:
        raise HTTPException(400, "Paste a screenplay (at least a few lines).")
    if len(script) > MAX_SCRIPT_CHARS:
        raise HTTPException(413, f"Script too long ({len(script)} chars; max {MAX_SCRIPT_CHARS}).")
    job = Job(script)
    JOBS[job.id] = job
    threading.Thread(target=_run_job, args=(job,), daemon=True).start()
    return {"job_id": job.id}


@app.get("/jobs/{job_id}")
def job_status(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Unknown job")
    return JSONResponse(job.status())


@app.get("/jobs/{job_id}/report.md")
def job_report_md(job_id: str):
    job = JOBS.get(job_id)
    if not job or not job.finished:
        raise HTTPException(404, "Report not ready")
    return PlainTextResponse(
        job.report_md,
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="clearcut-report-{job_id}.md"'},
    )


@app.get("/jobs/{job_id}/report.json")
def job_report_json(job_id: str):
    job = JOBS.get(job_id)
    if not job or not job.finished:
        raise HTTPException(404, "Report not ready")
    return JSONResponse(
        job.as_json(),
        headers={"Content-Disposition": f'attachment; filename="clearcut-report-{job_id}.json"'},
    )


@app.get("/healthz")
def healthz():
    return {"ok": True, "backend": "agent_engine" if AGENT_ENGINE_ID else "local", "model": MODEL}
