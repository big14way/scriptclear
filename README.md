# ClearCut — AI Script Clearance Agent

> Every script gets checked for names, brands, and songs that could get a studio sued. It costs thousands and takes a week, so indie filmmakers skip it. **ClearCut runs a full clearance pass in minutes, with cited web evidence for every flag.**

**Live demo:** https://clearcut-yy14.onrender.com · **Demo video (2:55):** https://youtu.be/F_YcwyvszF8

Built for **Agentic Cinema: The Blockbuster Hackathon** (Google Cloud × Parallel track).
Stack: **Google ADK** `SequentialAgent` → **Gemini on Vertex AI** → **Vertex AI Agent Engine** (hosting) → **Parallel Search API** (live, cited research) → **Cloud Run** (web UI).

---

## The problem

Before any film or series can be delivered to a distributor or streamer, the production must carry Errors & Omissions (E&O) insurance. E&O insurers require a **script clearance report**: a line-by-line review of the screenplay that identifies every element that could expose the production to a lawsuit and recommends a fix. Clearance is legal risk triage across five exposure categories:

1. **Defamation / right of publicity** — a fictional character shares a name (or name + profession + city) with a real, living person.
2. **Trademark / trade dress** — real brands, products, logos and company names, especially in a negative light.
3. **Copyright** — song lyrics or titles, poems, book passages, artwork, film clips quoted or performed.
4. **Real businesses and locations** — a fictional bar or clinic whose name matches a real one, or a real address used for a crime scene.
5. **Identifiers** — phone numbers, plates, emails, URLs that resolve to real people.

Today this costs low thousands of dollars per script, takes days to two weeks, and is repeated on every rewrite. It is manual: a human reads the script, extracts entities, and searches the web for each one. Indie, student and international productions routinely skip it and pay far more later in retakes, ADR and rejected deliveries. And a name that was safe last year may belong to a newly famous person this year: clearance needs **live web research with traceable sources**, not a static database.

## What ClearCut does

Paste a screenplay (plain text or Fountain). ClearCut:

1. **Extracts** every clearable element with its scene and quoted context (Gemini, structured JSON).
2. **Researches** each one live on the open web with category-specific queries via the **Parallel Search API** (a deterministic ADK stage: plain Python fan-out with 8 concurrent workers, no LLM in the loop, so it never stalls or hallucinates).
3. **Adjudicates** each entity RED / AMBER / GREEN against a clearance rubric using only the retrieved evidence, citing title + URL (Gemini).
4. **Reports**: a Markdown clearance report with an action-required table, review-recommended table, cleared list, recommended actions and tone-preserving substitutions. Downloadable as `.md` and `.json`.

## Architecture

```
Browser ── Cloud Run (FastAPI, web/main.py)
              │  POST /clear → stream_query(script)
              ▼
Vertex AI Agent Engine ──► ADK root_agent = SequentialAgent("clearcut_pipeline")
                                ├── extractor    LlmAgent (Gemini, JSON)          → state["entities"]
                                ├── researcher   custom BaseAgent (deterministic Python, no LLM)
                                │                  └── Parallel Search fan-out    → state["evidence"]
                                ├── adjudicator  LlmAgent (Gemini, JSON)          → state["findings"]
                                └── reporter     LlmAgent (Gemini, Markdown)      → state["report"]
                                                    │
                                                    ▼
                                      Parallel Search API (parallel-web SDK)
```

Risk rubric (in the adjudication prompt):

| Category | RED | AMBER | GREEN |
|---|---|---|---|
| Person | Real person with same name **and** profession/city/role, or a public figure | Common name, several partial matches | No plausible real match |
| Brand | Real trademark used negatively or as plot device | Real trademark, neutral mention | Fictional / none found |
| Song / work | Copyrighted work quoted, hummed or performed | Title mentioned only | Public domain / fictional |
| Business / location | Real business with same name + type + city, or real address tied to crime | Real business elsewhere | Fictional |
| Identifier | Resolves to a real number / plate / site | Format valid, unresolved | 555 / obviously fake |

## Why this doesn't exist yet, and how it pays for itself

**The gap.** Clearance is done by a handful of specialist houses and studio legal teams, by hand. There is no self-serve
tool because the job is not a database lookup: a name that was safe last year can belong to a newly famous person this
year, a company can rebrand, a song can change hands. It needs live web research with traceable sources for every
entity, then legal judgment on each one. That combination only became automatable with agent frameworks plus a search
API built for agents, and it is a research problem before it is a legal one.

**What is different about ClearCut.** Every flag carries a URL a human can check. The research stage is deterministic
Python fan-out (no LLM loop to stall or hallucinate), category-specific query strategies mirror how a clearance analyst
actually searches (name + profession + city; brand + owner + context; work + rights holder), and the report includes
what was cleared, not just what was flagged, because E&O reviewers need to see the work.

**Business model.** A full pass costs cents in Gemini and Parallel calls, versus $1,500–$4,000 and 5–14 days at a
clearance house. Pricing that follows from that:

| Tier | Who | Price |
|---|---|---|
| Per script | Indie producers, students, festival submissions | $49 per draft, first draft free |
| Writers' room | Series rooms clearing every revision | $199 / month, unlimited drafts, change-tracking between drafts |
| Professional | Clearance houses and studio legal | Per-seat licence, audit trail, export to E&O application format, Monitor API re-checks |

The professional tier is the largest revenue line: clearance houses do not lose work to ClearCut, they use it to
triage, so their analysts start from a cited report instead of a blank page.

## Where Google Cloud is used

| What | Where in code |
|---|---|
| **Gemini on Vertex AI** (every LLM stage; `GOOGLE_GENAI_USE_VERTEXAI=1`, model from `MODEL` env) | [`clearcut_agent/agent.py`](clearcut_agent/agent.py) |
| **Google ADK** (`SequentialAgent`, `LlmAgent`, custom `BaseAgent`, session state templating) | [`clearcut_agent/agent.py`](clearcut_agent/agent.py), [`clearcut_agent/tools/parallel_research.py`](clearcut_agent/tools/parallel_research.py) |
| **Vertex AI Agent Engine** (hosts the agent; web UI calls `agent_engines.get(...).stream_query`) | [`deploy_agent_engine.sh`](deploy_agent_engine.sh), [`web/main.py`](web/main.py) `_agent_engine_events` |
| **Cloud Run** (hosts the UI) | [`deploy_web.sh`](deploy_web.sh), [`Dockerfile`](Dockerfile) |
| **Cloud Trace** (agent tracing, `--trace_to_cloud`) | [`deploy_agent_engine.sh`](deploy_agent_engine.sh) |

## Where Parallel is used

The **Parallel Search API** is called **at runtime for every extracted entity** in
[`clearcut_agent/tools/parallel_research.py`](clearcut_agent/tools/parallel_research.py):

- `_queries_for()` builds a category-specific `objective` + up to 3 `search_queries` (a person check needs name + profession + city; a brand check needs owner and context; a song check needs rights holder / public-domain status).
- `_search_one()` calls `client.search(objective=..., search_queries=..., mode=..., advanced_settings={"max_results": ...})` and keeps `title`, `url` and the first excerpt per result.
- `research_entities()` fans out over all entities with a thread pool and writes the evidence into ADK session state for the adjudicator, which is instructed to cite only that evidence.

`parallel-web` is pinned in [`requirements.txt`](requirements.txt) and [`clearcut_agent/requirements.txt`](clearcut_agent/requirements.txt).

## Run it yourself

### Prerequisites
- Python 3.12, `gcloud` CLI, a Google Cloud project with billing.
- A Parallel API key from https://platform.parallel.ai.

### 1. Configure
```bash
git clone https://github.com/big14way/scriptclear.git && cd scriptclear
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # fill in GOOGLE_CLOUD_PROJECT, MODEL, PARALLEL_API_KEY
gcloud auth login && gcloud auth application-default login
./setup.sh                  # enables APIs, grants Cloud Run SA access to Vertex AI
./check_model.sh            # confirms $MODEL exists in Vertex AI Model Garden for your region
```

Three ways to reach Gemini are supported, selected in `.env` (see `.env.example`): Vertex AI on a billed project, Vertex AI Express Mode (API key, no billing), or the Gemini Developer API (AI Studio key). `MODEL_FALLBACK` names a second model that takes over for the rest of a run if the primary model's quota is exhausted; free-tier daily quotas on the newest model are small.

**Model ID is never hard-coded.** Open Vertex AI Model Garden (or run `./check_model.sh`), pick the newest Flash-class Gemini model, and set `MODEL` in `.env`. The agent refuses to start without it. As of September 2026 the newest GA Flash model is `gemini-3.8-flash` (released 2026-09-02), served from the `global` endpoint; `MODEL_LOCATION` pins the model endpoint independently of the Agent Engine / Cloud Run region.

### 2. Run locally (this is also how the hosted demo runs when no Agent Engine is configured)
```bash
adk web                     # ADK dev UI; select clearcut_pipeline and paste samples/the_last_shift.fountain
# or the full web UI, running the agent in-process (leave AGENT_ENGINE_ID empty):
uvicorn web.main:app --reload --port 8000   # http://localhost:8000
```

### 3. Deploy the agent to Vertex AI Agent Engine
```bash
./deploy_agent_engine.sh    # prints projects/.../reasoningEngines/<ID>; put <ID> in AGENT_ENGINE_ID in .env
```

### 4. Deploy the web UI to Cloud Run
```bash
./deploy_web.sh             # prints the public URL
```

### Hosting note for the hackathon demo
The public demo runs the same Docker image on Render's free tier with the ADK agent in-process (`AGENT_ENGINE_ID` unset) and Gemini reached through the Gemini Developer API, because the team's Google Cloud project has no billing account and Agent Engine / Cloud Run require one. Set `AGENT_ENGINE_ID` and `GOOGLE_GENAI_USE_VERTEXAI=1` on a billed project to switch to Vertex AI + Agent Engine with no code changes. `.github/workflows/keepalive.yml` pings the demo every 10 minutes so it never cold-starts.

## Sample script

[`samples/the_last_shift.fountain`](samples/the_last_shift.fountain) is an original ~6-page screenplay with deliberately planted risks in every category: a brand thrown at a villain, a neutral car mention, a fictional soda, a doctor with a common name + profession + city, a name-dropped senator, a hummed copyrighted song, a public-domain novel, a real-sounding dockside bar used for a drug deal, a famous address, a 555 phone number and a non-555 phone number.

## Repository layout

```
clearcut_agent/            ADK agent package (deployed to Agent Engine)
  agent.py                 root_agent: SequentialAgent of 4 stages
  prompts.py               extraction / adjudication / report prompts
  schemas.py               Pydantic models (Entity, Evidence, Finding)
  tools/parallel_research.py   Parallel Search fan-out tool
web/                       FastAPI UI (Cloud Run)
samples/                   demo screenplay
deploy_agent_engine.sh     adk deploy agent_engine
deploy_web.sh              gcloud run deploy
setup.sh / check_model.sh  project setup / Model Garden check
```

## Limitations and next steps
- ClearCut is a research aid, not legal advice; confirm findings with your E&O carrier.
- Next: Parallel Monitor API to re-flag a script when the web changes; per-territory rules; PDF / Final Draft ingestion; export to E&O application format.

## License
MIT — see [LICENSE](LICENSE).
