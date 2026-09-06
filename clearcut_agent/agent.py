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
from google.adk.agents import LlmAgent, SequentialAgent
from google.adk.models.google_llm import Gemini
from google.adk.tools import FunctionTool
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


class VertexGemini(Gemini):
    """ADK Gemini model whose client always targets Vertex AI at MODEL_LOCATION."""

    @cached_property
    def api_client(self) -> Client:
        http_options = types.HttpOptions(headers=self._tracking_headers(), retry_options=self.retry_options)
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


# Optional second model used when the primary model's quota is exhausted (HTTP 429).
MODEL_FALLBACK = os.getenv("MODEL_FALLBACK", "").strip()
_TRANSIENT = [500, 502, 503, 504]


def _retry_delay_seconds(err: Exception, default: float = 20.0) -> float:
    m = re.search(r"retry in ([0-9.]+)s", str(err), re.IGNORECASE)
    return min(float(m.group(1)) + 1, 90.0) if m else default


def _is_daily_quota(err: Exception) -> bool:
    text = str(err)
    return "PerDay" in text or "per day" in text.lower() or "RESOURCE_EXHAUSTED" in text and "Please retry in" not in text


class FallbackGemini(VertexGemini):
    """Gemini with quota handling.

    * Transient 5xx errors are retried by the google-genai client (retry_options).
    * HTTP 429 with a short retry hint (per-minute quota) is waited out, up to 3 times.
    * HTTP 429 that signals an exhausted daily quota (or repeated 429s) switches to MODEL_FALLBACK
      for the rest of the process, so a clearance run never dies mid-pipeline.
    """

    _use_fallback: ClassVar[bool] = False

    def _fallback(self) -> VertexGemini:
        return VertexGemini(model=MODEL_FALLBACK, retry_options=self.retry_options)

    async def generate_content_async(self, llm_request, stream: bool = False):
        if MODEL_FALLBACK and FallbackGemini._use_fallback and MODEL_FALLBACK != self.model:
            llm_request.model = MODEL_FALLBACK
            async for r in self._fallback().generate_content_async(llm_request, stream=stream):
                yield r
            return
        for attempt in range(4):
            try:
                async for r in super().generate_content_async(llm_request, stream=stream):
                    yield r
                return
            except Exception as e:  # ADK wraps 429 in _ResourceExhaustedError; match on text to stay version-safe
                if "429" not in str(e):
                    raise
                can_fallback = bool(MODEL_FALLBACK) and MODEL_FALLBACK != self.model
                if can_fallback and (_is_daily_quota(e) or attempt >= 2):
                    logger.warning("Quota exhausted on %s; switching to %s for the rest of this run.", self.model, MODEL_FALLBACK)
                    FallbackGemini._use_fallback = True
                    llm_request.model = MODEL_FALLBACK
                    async for r in self._fallback().generate_content_async(llm_request, stream=stream):
                        yield r
                    return
                if attempt == 3:
                    raise
                delay = _retry_delay_seconds(e)
                logger.warning("429 on %s; retrying in %.0fs (attempt %d/3).", self.model, delay, attempt + 1)
                await asyncio.sleep(delay)


def _model() -> VertexGemini:
    return FallbackGemini(model=MODEL, retry_options=types.HttpRetryOptions(
            initial_delay=3, attempts=6, max_delay=30, exp_base=1.6, http_status_codes=_TRANSIENT
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

researcher = LlmAgent(
    name="researcher",
    model=_model(),
    description="Runs live Parallel Search research for every entity.",
    instruction=(
        "Call the research_entities tool exactly once. After it returns, reply with a single short line "
        "stating how many entities were researched. Do not call any other tool."
    ),
    tools=[FunctionTool(research_entities)],
    generate_content_config=_text_cold,
    include_contents="none",  # the tool reads from state; the script text is not needed here
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
