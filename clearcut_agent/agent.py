"""ClearCut root agent: a deterministic four-stage ADK SequentialAgent.

extractor (Gemini) -> researcher (Parallel Search fan-out tool) -> adjudicator (Gemini) -> reporter (Gemini)
"""

import asyncio
import logging
import os
import re
import time
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
REQUEST_TIMEOUT_MS = int(os.getenv("GEMINI_REQUEST_TIMEOUT_MS", "45000"))


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
    """Gemini with quota- and saturation-aware scheduling across (model, key) slots.

    Slots are ordered model-major: MODEL on every key first, then each fallback model on every key, so the strongest
    model is used as long as any key has quota for it.
      * transient 5xx are retried by the google-genai client (retry_options);
      * a per-minute 429 is waited out (up to 3 times), then the slot is rested briefly;
      * a daily-quota 429 rests that slot for an hour (quota is per project per model);
      * a saturated model (503 / timeout) rests every slot of that model for 10 minutes, since saturation is not per key.
    Each request starts from the first un-rested slot, so a run never dies while any slot is usable.
    """

    QUOTA_REST_S: ClassVar[int] = 3600
    MINUTE_REST_S: ClassVar[int] = 120
    SATURATED_REST_S: ClassVar[int] = 600
    ALL_RESTED_WAIT_S: ClassVar[int] = 30
    TOTAL_BUDGET_S: ClassVar[int] = int(os.getenv("GEMINI_TOTAL_BUDGET_S", "480"))
    _slots: ClassVar[list[tuple[str, str | None]]] = [(m, k) for m in MODEL_CHAIN for k in (API_KEYS or [None])]
    _rested: ClassVar[dict[int, float]] = {}  # slot index -> epoch seconds until which it is rested
    _cache: ClassVar[dict[int, VertexGemini]] = {}

    def _llm_for_slot(self, slot: int) -> VertexGemini:
        if slot not in FallbackGemini._cache:
            model, key = FallbackGemini._slots[slot]
            llm = VertexGemini(model=model, retry_options=self.retry_options)
            if key:
                llm.__dict__["api_client"] = self._build_client(api_key=key)  # pre-seed the cached_property
            FallbackGemini._cache[slot] = llm
        return FallbackGemini._cache[slot]

    def _build_client(self, api_key: str | None) -> Client:
        http_options = types.HttpOptions(
            headers=self._tracking_headers(), retry_options=self.retry_options, timeout=REQUEST_TIMEOUT_MS
        )
        use_vertex = os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "1").strip().lower() in ("1", "true", "yes")
        if not use_vertex:
            return Client(api_key=api_key, http_options=http_options)
        return Client(vertexai=True, api_key=api_key, http_options=http_options)

    @classmethod
    def _pick_slot(cls, tried: set[int]) -> int | None:
        now = time.time()
        for i in range(len(cls._slots)):
            if i not in tried and cls._rested.get(i, 0) <= now:
                return i
        return None

    @classmethod
    def _rest(cls, slot: int, seconds: float, whole_model: bool = False) -> None:
        until = time.time() + seconds
        model = cls._slots[slot][0]
        for i, (m, _) in enumerate(cls._slots):
            if i == slot or (whole_model and m == model):
                cls._rested[i] = max(cls._rested.get(i, 0), until)

    async def generate_content_async(self, llm_request, stream: bool = False):
        tried: set[int] = set()
        minute_waits = 0
        last_error: Exception | None = None
        deadline = time.time() + self.TOTAL_BUDGET_S
        while True:
            slot = self._pick_slot(tried)
            if slot is None:
                # Every slot is rested (saturation everywhere). Rather than failing the run, wait for the earliest
                # rest to expire and try again, until the overall budget for this request is spent.
                if not FallbackGemini._slots or time.time() >= deadline:
                    if last_error:
                        raise last_error
                    raise RuntimeError("No Gemini model/key slot is available; check MODEL, MODEL_FALLBACK and API keys.")
                tried.clear()
                soonest = min(FallbackGemini._rested.get(i, 0) for i in range(len(FallbackGemini._slots)))
                wait = max(5.0, min(soonest - time.time(), self.ALL_RESTED_WAIT_S))
                logger.warning("All Gemini slots are resting; waiting %.0fs before retrying.", wait)
                await asyncio.sleep(wait)
                # Un-rest the earliest slots so the retry can proceed even before their nominal rest ends.
                for i in sorted(range(len(FallbackGemini._slots)), key=lambda i: FallbackGemini._rested.get(i, 0))[:2]:
                    FallbackGemini._rested[i] = 0
                continue
            model, _ = FallbackGemini._slots[slot]
            llm_request.model = model
            try:
                async for r in VertexGemini.generate_content_async(self._llm_for_slot(slot), llm_request, stream=stream):
                    yield r
                return
            except Exception as e:  # ADK wraps 429 in _ResourceExhaustedError; match on text to stay version-safe
                last_error = e
                text = f"{type(e).__name__}: {e}"
                saturated = any(k in text for k in ("503", "UNAVAILABLE", "high demand", "Timeout", "timed out", "504"))
                if "429" not in text and not saturated:
                    raise
                if saturated:
                    logger.warning("Model %s saturated (%s); resting it for %ds.", model, text[:60], self.SATURATED_REST_S)
                    self._rest(slot, self.SATURATED_REST_S, whole_model=True)
                    tried.add(slot)
                elif _is_daily_quota(e):
                    logger.warning("Daily quota exhausted for %s on key #%d; resting that slot.", model, slot % max(len(API_KEYS), 1) + 1)
                    self._rest(slot, self.QUOTA_REST_S)
                    tried.add(slot)
                elif minute_waits < 3:
                    minute_waits += 1
                    delay = _retry_delay_seconds(e)
                    logger.warning("Per-minute 429 on %s; retrying in %.0fs (%d/3).", model, delay, minute_waits)
                    await asyncio.sleep(delay)
                else:
                    self._rest(slot, self.MINUTE_REST_S)
                    tried.add(slot)
                    minute_waits = 0


def _model() -> VertexGemini:
    return FallbackGemini(model=MODEL, retry_options=types.HttpRetryOptions(
            initial_delay=1, attempts=2, max_delay=4, exp_base=2, http_status_codes=_TRANSIENT
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
