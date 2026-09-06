"""ClearCut root agent: a deterministic four-stage ADK SequentialAgent.

extractor (Gemini) -> researcher (Parallel Search fan-out tool) -> adjudicator (Gemini) -> reporter (Gemini)
"""

import os
from functools import cached_property
from pathlib import Path

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


class VertexGemini(Gemini):
    """ADK Gemini model whose client always targets Vertex AI at MODEL_LOCATION."""

    @cached_property
    def api_client(self) -> Client:
        http_options = types.HttpOptions(headers=self._tracking_headers())
        api_key = os.getenv("GOOGLE_API_KEY")
        project = os.getenv("GOOGLE_CLOUD_PROJECT")
        if api_key and not project:
            # Vertex AI Express Mode: API key, no project/location needed.
            return Client(vertexai=True, api_key=api_key, http_options=http_options)
        return Client(vertexai=True, project=project or None, location=MODEL_LOCATION, http_options=http_options)


def _model() -> VertexGemini:
    return VertexGemini(model=MODEL, retry_options=types.HttpRetryOptions(initial_delay=10, attempts=12, max_delay=60, exp_base=1.5))


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
    instruction=REPORT + "\n\nFINDINGS:\n{findings}\n\nENTITIES:\n{entities}",
    generate_content_config=_text_cold,
    include_contents="none",
    output_key="report",
)

root_agent = SequentialAgent(
    name="clearcut_pipeline",
    description="Script clearance: extract -> research (Parallel Search) -> adjudicate -> report",
    sub_agents=[extractor, researcher, adjudicator, reporter],
)
