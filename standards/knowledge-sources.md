# Knowledge-Source Routing Standard

**Type:** Advisory
**Applies to:** All MAS agents and skills that retrieve knowledge
**Source:** proj-YYYYMMDD-NNN-skill-vs-episodic-audit (knowledge-source overlap audit)

When an agent needs to "look something up," choose the source by the **shape of the answer**.
graphify and the episodic-DB query stack are complementary, not redundant: graphify answers
*source-structure* questions; the episodic stack answers *runtime-history*, *curated-fact*, and
*inventory* questions. Don't reach for the wrong one.

## Decision matrix

| Knowledge need (what you're asking) | First choice | Fallback | When NOT to use the first choice |
|---|---|---|---|
| "How does this code work? What calls X? Trace the data flow / architecture." | **graphify** (`/graphify query`, `path`, `explain`) | read source directly; `mas_codebase` for the file list | Not for runtime history or external prior art. Don't build a graph for a one-off — just read the file. |
| "What did we decide / what happened / why was X chosen in past projects?" | **`db.semantic_search`** (FTS5 over `agent_events`) | `query_project_history` / `query_agent_context` | Never for source structure — it indexes events, not code. |
| "What modules / agents / skills / policies exist, and where?" | **registry query** (`mas registry list`; `mas_codebase`; `capability_registry`) | filesystem glob | Not for *how* they work — that's graphify (registry is inventory only). |
| "Cross-project lessons / agent-performance patterns / curated architecture facts." | **`agent_graph`** (`query_graph_node` / `query_graph_edges`) | `semantic_search` over events | Not for live source structure or raw event detail. |
| "External best practice / prior art / published patterns / domain grounding." | **notebooklm** | `domain_expert` in-context knowledge; web search | Not for repo-internal questions — use graphify/registry. |
| "Reason over a document input (PDF / DOCX / PPTX / XLSX / CSV)." | **markitdown** first (convert), then route to a row above | read tool for plain text | It is input prep, not retrieval. |

## Rule of thumb

Relationships/structure → **graphify**. Exact history → **SQL events (+FTS)**. Inventory →
**registry**. Curated lessons → **agent_graph**. External prior art → **notebooklm**. Document
input → **markitdown** first.

## Tie-breakers

- **"Codebase" (structure vs inventory):** behaviour/relationships → graphify (deep, on-demand,
  costly); existence/location → registry/`mas_codebase` (shallow, live, cheap).
- **"Memory" (raw vs distilled):** raw what-happened → `semantic_search` over events; distilled
  lesson/pattern → `agent_graph`.
- **Internal vs external:** anything about *this repo / these projects* stays in graphify + the
  episodic stack; only *outside* knowledge goes to notebooklm.
- **graphify cost gate:** only build `graphify-out/` when relationship questions recur often enough
  to amortise the one-time LLM-extraction cost; otherwise read source on demand.

## Why (evidence)

graphify = deep structural graph over source/docs (skills/graphify/SKILL.md). `db.semantic_search`
= FTS5/BM25 over runtime `agent_events`, not code (`mas/core/db.py`). `agent_graph` = curated MAS
facts/lessons keyed by agent (`prompt_assembler._graph_context`). `mas_codebase` = a shallow path
index (`registry_seed.py`; `description` is NULL). The only thin overlap is graphify vs
`mas_codebase` — deep structure vs shallow inventory — and it is depth-differentiated, not
duplication.
