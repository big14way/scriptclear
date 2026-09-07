# ClearCut demo — voiceover script

Read at a calm pace (~140 words/min). Each block is timed to the Remotion timeline in `demo_recording_plan.md`.
Total ≈ 400 words ≈ 2:50. Do not improvise; if a take runs long, cut from block 6 first, never from block 4.

---

**[0:00–0:08] Title card**
Every script gets checked for names, brands, and songs that could get a studio sued.

**[0:08–0:22] Problem card**
It's called script clearance. Insurers require it before a film can be delivered. A clearance house charges thousands
of dollars and takes up to two weeks — and every rewrite starts the clock again. So indie filmmakers skip it, and pay
far more later in reshoots and blurred logos.

**[0:22–0:38] Architecture card**
ClearCut is a four-stage agent pipeline built with Google's Agent Development Kit. Gemini extracts every clearable
element and judges the risk. Parallel's Search API does live, cited web research on each one. The stages run as a
deterministic sequence, so the same script gives the same report.

**[0:38–1:05] Live app, loading and running**
Here's the app. I'll load our sample screenplay, The Last Shift — an original script with risks planted in every
category — and run clearance. You can watch each stage: extraction, then Parallel researching every entity in
parallel, then adjudication, then the report. The whole pass takes about a minute and a half.

**[1:05–2:00] Results**
Four red flags, seven to review, and the rest cleared. First: Coca-Cola. A character throws the can at the villain —
a famous trademark in a negative use. Every flag links to real evidence — this is the trademark record Parallel found
just now — with a recommended fix and a substitution that keeps the tone. Second: Purple Rain. The character hums it
and sings a line; that's a performance of a copyrighted song, and ClearCut suggests an original replacement. Under
review recommended: a doctor whose name and specialty partially match real physicians — that's a live web check, so
the answer can change as the web changes. And the cleared list matters too: Toyota in a neutral mention, Moby-Dick in
the public domain, a 555 phone number. Everything downloads as Markdown or JSON for the E&O application.

**[2:00–2:25] Code**
Parallel is called at runtime for every entity, right here — category-specific queries, eight concurrent searches,
excerpts and URLs stored for the judge stage. The pipeline is an ADK SequentialAgent on Gemini 3.8 Flash. It's open
source under MIT, with a Docker image that deploys to Cloud Run or Agent Engine on a billed project.

**[2:25–2:45] Impact card**
Producers can clear every draft, not just the shooting script. Clearance professionals get a cited first pass instead
of a blank page. Next: Parallel's Monitor API to re-flag a script automatically when the web changes.

**[2:45–2:55] Closing card**
ClearCut. Open source. Try it at the link.

---

## Caption text for Remotion (verbatim, one per moment)
- Problem card: `$1,500–$4,000 per script · 5–14 days · every rewrite`
- Wait jump-cut: `≈ 100 s real time`
- Evidence click: `Live evidence · Parallel Search API`
- Purple Rain fix: `Recommended fix + safe substitution`
- Code, Parallel file: `client.search(...) — called at runtime for every entity`
- Code, agent file: `Google ADK SequentialAgent · Gemini 3.8 Flash`
- Closing: `github.com/big14way/scriptclear · clearcut-yy14.onrender.com`
