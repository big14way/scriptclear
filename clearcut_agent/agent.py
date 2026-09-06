"""ClearCut root agent: a deterministic four-stage ADK SequentialAgent.

extractor (Gemini) -> researcher (Parallel Search fan-out tool) -> adjudicator (Gemini) -> reporter (Gemini)
"""

import asyncio
import logging
import os
import re
from functools import cached_property
from pathlib import Path
from typing import ClassVar

from dotenv import load_dotenv
from typing import AsyncGenerator

from google.adk.agents import BaseAgent, LlmAgent, SequentialAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from google.adk.models.google_llm import Gemini
from google.adk.tools.tool_context import ToolContext
from google.genai import Client, types

from .prompts import ADJUDICATE, EXTRACT, REPORT
from .tools.parallel_research import research_entities

# Load .env from the agent folder or the repo root (adk web / local runs). Agent Engine injects env directly.
for _candidate in (Path(__file__).parent / ".env", Path(__file__).parent.parent / ".env"):
    if _candidate.exists():
        load_dotenv(_candidate, override=False)

MODEL = os.getenv("MODEL")
if not MODEL:
    raise RuntimeError(
        "MODEL environment variable is not set. Check Vertex AI Model Garden for the current "
        "Gemini Flash model ID and set MODEL in .env (see .env.example)."
    )

# Current Gemini Flash models on Vertex AI are served from the `global` endpoint (and the us/eu multi-regions),
# not from single regions such as us-central1. Agent Engine itself deploys to a region, so the model location is
# pinned separately from GOOGLE_CLOUD_LOCATION.
MODEL_LOCATION = os.getenv("MODEL_LOCATION", "global")
logger = logging.getLogger(__name__)
# Hard per-request timeout: saturated models sometimes stall a request for many minutes; fail fast and retry instead.
REQUEST_TIMEOUT_MS = int(os.getenv("GEMINI_REQUEST_TIMEOUT_MS", "60000"))


class VertexGemini(Gemini):
    """ADK Gemini model whose client always targets Vertex AI at MODEL_LOCATION."""

    @cached_property
    def api_client(self) -> Client:
        http_options = types.HttpOptions(headers=self._tracking_headers(), retry_options=self.retry_options, timeout=REQUEST_TIMEOUT_MS)
        api_key = os.getenv("GOOGLE_API_KEY")
        project = os.getenv("GOOGLE_CLOUD_PROJECT")
        use_vertex = os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "1").strip().lower() in ("1", "true", "yes")
        if not use_vertex:
            # Gemini Developer API (AI Studio key). Allowed by the hackathon rules via google-genai/google-adk.
            return Client(api_key=api_key, http_options=http_options)
        if api_key and not project:
            # Vertex AI Express Mode: API key, no project/location needed.
            return Client(vertexai=True, api_key=api_key, http_options=http_options)
        return Client(vertexai=True, project=project or None, location=MODEL_LOCATION, http_options=http_options)


# Quota resilience. Free-tier daily quotas per model are small (20 requests/day on the newest Flash at the time of
# writing), so a run may walk a chain of fallback models and a pool of API keys (one quota bucket per project).
#   MODEL_FALLBACK   comma-separated models tried in order after MODEL when a daily quota is exhausted
#   GOOGLE_API_KEYS  comma-separated API keys (Developer API / Express Mode); defaults to GOOGLE_API_KEY
MODEL_CHAIN = [MODEL] + [m.strip() for m in os.getenv("MODEL_FALLBACK", "").split(",") if m.strip()]
API_KEYS = [k.strip() for k in (os.getenv("GOOGLE_API_KEYS") or os.getenv("GOOGLE_API_KEY") or "").split(",") if k.strip()]
_TRANSIENT = [500, 502, 503, 504]


def _retry_delay_seconds(err: Exception, default: float = 20.0) -> float:
    m = re.search(r"retry in ([0-9.]+)s", str(err), re.IGNORECASE)
    return min(float(m.group(1)) + 1, 90.0) if m else default


def _is_daily_quota(err: Exception) -> bool:
    text = str(err)
    if "PerDay" in text or "per day" in text.lower() or "free_tier_requests" in text:
        return True
    return "RESOURCE_EXHAUSTED" in text and "Please retry in" not in text


class FallbackGemini(VertexGemini):
    """Gemini with quota handling.

    * Transient 5xx errors are retried by the google-genai client (retry_options).
    * HTTP 429 with a short retry hint (per-minute quota) is waited out, up to 3 times.
    * HTTP 429 that signals an exhausted daily quota (or repeated 429s) advances to the next (key, model) slot for the
      rest of the process: every model in MODEL_CHAIN on the current key, then the next key from the start of the chain.
      A clearance run therefore never dies mid-pipeline while any slot has quota left.
    """

    _slot: ClassVar[int] = 0  # index into the (key, model) grid, shared across the process
    _slots: ClassVar[list[tuple[str | None, str]]] = [(k, m) for k in (API_KEYS or [None]) for m in MODEL_CHAIN]

    _cache: ClassVar[dict[int, VertexGemini]] = {}

    def _llm_for_slot(self, slot: int) -> VertexGemini:
        """One cached VertexGemini per (key, model) slot so clients are reused across requests."""
        if slot not in FallbackGemini._cache:
            key, model = FallbackGemini._slots[slot]
            llm = VertexGemini(model=model, retry_options=self.retry_options)
            if key:
                llm.__dict__["api_client"] = self._build_client(api_key=key)  # pre-seed the cached_property
            FallbackGemini._cache[slot] = llm
        return FallbackGemini._cache[slot]

    def _build_client(self, api_key: str | None) -> Client:
        http_options = types.HttpOptions(headers=self._tracking_headers(), retry_options=self.retry_options, timeout=REQUEST_TIMEOUT_MS)
        use_vertex = os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "1").strip().lower() in ("1", "true", "yes")
        if not use_vertex:
            return Client(api_key=api_key, http_options=http_options)
        return Client(vertexai=True, api_key=api_key, http_options=http_options)

    @staticmethod
    def _advance() -> bool:
        if FallbackGemini._slot + 1 >= len(FallbackGemini._slots):
            return False
        FallbackGemini._slot += 1
        return True

    async def generate_content_async(self, llm_request, stream: bool = False):
        attempt = 0
        while True:
            key, model = FallbackGemini._slots[FallbackGemini._slot]
            llm = self._llm_for_slot(FallbackGemini._slot)
            llm_request.model = model
            try:
                async for r in VertexGemini.generate_content_async(llm, llm_request, stream=stream):
                    yield r
                return
            except Exception as e:  # ADK wraps 429 in _ResourceExhaustedError; match on text to stay version-safe
                text = f"{type(e).__name__}: {e}"
                saturated = any(k in text for k in ("503", "UNAVAILABLE", "high demand", "Timeout", "timed out", "504"))
                if "429" not in text and not saturated:
                    raise
                if saturated or _is_daily_quota(e) or attempt >= 2:
                    if not self._advance():
                        raise
                    key, model = FallbackGemini._slots[FallbackGemini._slot]
                    logger.warning("Quota exhausted or model saturated; switching to model %s (key #%d) for the rest of this run.",
                                   model, FallbackGemini._slot // len(MODEL_CHAIN) + 1)
                    attempt = 0
                    continue
                attempt += 1
                delay = _retry_delay_seconds(e)
                logger.warning("429 on %s; retrying in %.0fs (attempt %d/3).", model, delay, attempt)
                await asyncio.sleep(delay)


def _model() -> VertexGemini:
    return FallbackGemini(model=MODEL, retry_options=types.HttpRetryOptions(
            initial_delay=2, attempts=3, max_delay=8, exp_base=2, http_status_codes=_TRANSIENT
        ))


_json_cold = types.GenerateContentConfig(temperature=0, response_mime_type="application/json")
_text_cold = types.GenerateContentConfig(temperature=0)

extractor = LlmAgent(
    name="extractor",
    model=_model(),
    description="Extracts every clearable entity from the screenplay as JSON.",
    instruction=EXTRACT,
    generate_content_config=_json_cold,
    output_key="entities",  # JSON string -> state["entities"]
)

class ResearcherAgent(BaseAgent):
    """Deterministic stage: runs the Parallel Search fan-out directly (no LLM call, nothing to stall or hallucinate)."""

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        tool_context = ToolContext(ctx)
        result = await asyncio.to_thread(research_entities, tool_context)
        actions = tool_context.actions  # carries the state_delta written by the tool
        yield Event(
            author=self.name,
            invocation_id=ctx.invocation_id,
            actions=actions,
            content=types.Content(
                role="model",
                parts=[
                    types.Part(function_response=types.FunctionResponse(name="research_entities", response=result)),
                    types.Part(text=f"Researched {result['researched']} entities via Parallel Search."),
                ],
            ),
        )


researcher = ResearcherAgent(
    name="researcher",
    description="Runs live Parallel Search research for every entity.",
)

adjudicator = LlmAgent(
    name="adjudicator",
    model=_model(),
    description="Assigns RED/AMBER/GREEN risk to each entity using only the cited evidence.",
    instruction=ADJUDICATE + "\n\nENTITIES:\n{entities}\n\nEVIDENCE:\n{evidence}",
    generate_content_config=_json_cold,
    include_contents="none",
    output_key="findings",  # JSON string -> state["findings"]
)

reporter = LlmAgent(
    name="reporter",
    model=_model(),
    description="Assembles the final Markdown clearance report.",
    instruction=REPORT + "\n\nREPORT DATE: {run_date}\n\nFINDINGS:\n{findings}\n\nENTITIES:\n{entities}",
    generate_content_config=_text_cold,
    include_contents="default",  # sees the screenplay (for the title) plus prior stage outputs
    output_key="report",
)

root_agent = SequentialAgent(
    name="clearcut_pipeline",
    description="Script clearance: extract -> research (Parallel Search) -> adjudicate -> report",
    sub_agents=[extractor, researcher, adjudicator, reporter],
)
