# ClearCut demo video — recording plan

Target: one video, **2:55 max** (judges stop at 3:00). Final assembly in Remotion: title/impact cards are rendered
in Remotion; the screen clips below are recorded once, trimmed, and captioned. Record everything **before 10:00 WAT**
(US night) when the Gemini free tier is fast; run one warm-up clearance first and confirm it finishes in about 100 s.

## Setup (10 minutes, do once)
- Browser: Chrome or Brave, fresh window, **1920×1080**, hide bookmarks bar, zoom **110%**, dark theme is built in.
- Close every other tab. Open only: `https://clearcut-yy14.onrender.com`.
- Recorder: QuickTime (File → New Screen Recording, capture the browser window) or Screen Studio. 30 fps is fine.
  Record **without** microphone; voiceover is added in Remotion.
- Editor: VS Code, dark theme, font size 16+, minimap off, sidebar hidden. Open these two files in separate tabs:
  - `clearcut_agent/tools/parallel_research.py` scrolled so `_search_one` and the `client.search(` call fill the screen
  - `clearcut_agent/agent.py` scrolled so `root_agent = SequentialAgent(` and the four sub-agents fill the screen
- Second browser tab (open only when needed): `https://github.com/big14way/scriptclear` (shows MIT license in About).
- Have `docs/demo_voiceover_script.md` open on a phone; the shot numbers below match its sections.

## Clips to record (record each as its own file; names are used in Remotion)

### CLIP-A `app_run.mov` — the live run (record in one take, ~2 min, will be jump-cut)
1. Land on the page. Pause 2 s on the hero text so the tagline is readable.
2. Click **Load sample script**. Scroll the textarea slowly for ~3 s so the screenplay formatting is visible
   (stop where "Coca-Cola" or "PURPLE RAIN" is on screen if you can).
3. Click **Run clearance**. Stay on the progress panel. Let it record the whole run; do not touch anything.
   The four stage cards light up: Extract → Research (shows "Parallel Search fan-out on N entities") → Adjudicate → Report.
   In Remotion you will jump-cut the middle and overlay the caption `≈ 100 s real time`.
4. When the result appears, let the page auto-scroll to the summary strip and hold 3 s.

### CLIP-B `app_results.mov` — exploring the report (~90 s, will be trimmed to ~55 s)
1. Start on the RED / AMBER / GREEN summary strip. Hold 2 s.
2. Scroll slowly through **Action required**. Stop on the **Coca-Cola** row. Hold 3 s.
3. Click the **first evidence link** in the Coca-Cola row (a trademark page opens in a new tab). Hold 3 s on the real
   page, then close the tab to come back.
4. Scroll to the **Purple Rain** row. Hold 3 s on its Fix column (recommended action + substitution).
5. Scroll to **Review recommended** (AMBER), pause 2 s on the doctor / Rusty Anchor rows.
6. Scroll to **Cleared** and hold 3 s (Toyota, Moby-Dick, 555 number should be there).
7. Click the **Findings & evidence** tab. Scroll slowly for 5 s so the evidence links and substitutions are visible.
8. Click **Download .md**. Show the downloaded file bar / Finder for 2 s.

### CLIP-C `code.mov` — the integration proof (~30 s)
1. VS Code on `parallel_research.py`: hold 4 s with `_get_client().search(` visible. Slowly move the cursor to that line.
2. Switch tab to `agent.py`: hold 4 s on `root_agent = SequentialAgent(` with the four sub-agents listed.
3. Switch to the browser tab with the GitHub repo. Hold 3 s so "MIT License" and the README's live URL are visible.

### CLIP-D (optional, 5 s) `mobile.mov`
Open the live URL on your phone and screen-record the summary strip after a run. Proves it works on mobile data.

## Remotion assembly (timeline)
| Time | Source | On-screen caption |
|---|---|---|
| 0:00–0:08 | Remotion title card: "ClearCut — AI script clearance with cited evidence" | — |
| 0:08–0:22 | Remotion card: "$1,500–$4,000 per script · 5–14 days · repeated on every rewrite" | "Indie filmmakers skip it" |
| 0:22–0:38 | Remotion architecture card (4 boxes: Extract → Research → Adjudicate → Report; Parallel logo on Research; Gemini on the other three; "Google ADK SequentialAgent" underneath) | — |
| 0:38–1:05 | CLIP-A steps 1–3, jump-cut the wait | "≈ 100 s real time" during the wait |
| 1:05–2:00 | CLIP-A step 4 + CLIP-B steps 1–8 | "Live trademark evidence from Parallel" on the link click; "Safe substitution" on Purple Rain fix |
| 2:00–2:25 | CLIP-C | "Parallel Search called at runtime for every entity" · "ADK SequentialAgent · Gemini 3.8 Flash" |
| 2:25–2:45 | Remotion impact card (three bullets from the script) | — |
| 2:45–2:55 | Remotion closing card: repo URL, live URL, "MIT · Parallel track" | — |

Export 1080p, upload to YouTube as **Public** (not unlisted), title "ClearCut — AI Script Clearance Agent (Agentic
Cinema Hackathon)". Paste the link into Devpost and the README.
