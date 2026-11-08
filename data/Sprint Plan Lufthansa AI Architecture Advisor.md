# Sprint Plan: Lufthansa AI Architecture Advisor

This plan breaks the build of the AI Architecture Advisor into seven sprints. It is written to be handed to Claude Code one sprint at a time, together with the two input files.

## 1. Project Context

The AI Architecture Advisor is a proof of concept built for Lufthansa as part of the FH Aachen course IT-Consulting in Practice. An enterprise architect asks a question in plain language, such as “What would be affected if we replaced the Booking System?”, and receives an explained answer showing the affected capabilities, platforms and domains, with every statement labelled and traced back to the P180 reference architecture.

The build uses two inputs, both stored in the repository under `data/`:

| File | Role |
| --- | --- |
| `Lufthansa_Architecture_Data_P180_All_Domains.xlsx` | The architecture model. Its Elements sheet (342 rows) becomes the graph nodes; its Relationships sheet (473 rows) becomes the graph relationships. It is the only source of architecture facts. |
| `P180.pdf` | The Open Group Commercial Aviation Reference Architecture. It is the evidence source: answers cite its pages. |

The Read Me sheet in the Excel file defines every element type, relationship type and status value, and should be read before Sprint 1.

## 2. Tech Stack and Project Structure

| Layer | Technology |
| --- | --- |
| Language | Python |
| Graph and vector database | Neo4j AuraDB Free (graph plus native vector index) |
| LLM and embeddings | OpenAI API |
| PDF processing | PyMuPDF |
| Orchestration | Plain Python with the OpenAI SDK and Pydantic; no agent framework |
| Interface | Streamlit, with streamlit-agraph for the mind map |
| Hosting | Streamlit Community Cloud, deployed from GitHub |

Secrets (Neo4j URI, user and password, OpenAI key) live in `.streamlit/secrets.toml`, which is listed in `.gitignore` and never committed.

The repository is organised as follows:

| Path | Purpose |
| --- | --- |
| `data/` | The Excel model and P180.pdf |
| `scripts/load_graph.py` | Loads the Excel into Neo4j |
| `scripts/load_evidence.py` | Loads P180 pages and embeddings into Neo4j |
| `backend/graph.py` | Neo4j connection and shared helpers |
| `backend/impact.py` | The predefined impact queries |
| `backend/evidence.py` | Evidence retrieval |
| `backend/llm.py` | Question parsing and answer writing |
| `app.py` | The Streamlit interface |
| `eval/` | Test questions, expected answers and the evaluation script |
| `tests/` | Automated tests for the loaders and queries |

## 3. Graph Model

The Excel maps one to one onto Neo4j. Every element gets the shared label `Element` plus a label for its type, and keeps its Name, Source and Notes as properties.

| Excel element type | Neo4j label | Count |
| --- | --- | --- |
| Domain | Domain | 10 |
| Subdomain | Subdomain | 42 |
| Application | Application | 49 |
| Capability | Capability | 134 |
| Data Object | DataObject | 47 |
| Platform | Platform | 19 |
| Technology Component | TechnologyComponent | 41 |

Every relationship keeps its Status, Source and Notes as properties, so each link carries its own fact or inference label.

| Excel relationship type | Neo4j type | Count | Meaning |
| --- | --- | --- | --- |
| Belongs To | BELONGS\_TO | 158 | Hierarchy and membership: subdomain to domain, application to subdomain or domain, component to platform, data object to container |
| Realizes | REALIZES | 135 | Application supports a capability |
| Associated With | ASSOCIATED\_WITH | 54 | Line between two data objects; direction has no meaning |
| Used In | USED\_IN | 126 | Data object or platform appears in a domain |
| Depends On | DEPENDS\_ON | 0 | Reserved for dependencies added later as Inferred or Illustrative |

Sprint 2 adds one more node type, Evidence (one per P180 page), linked to elements with SUPPORTED\_BY.

## 4. Sprint 0: Setup

**Goal:** every team member can run an empty Streamlit app that connects to the shared Neo4j database and the OpenAI API.

**Done by the team (outside Claude Code):**

1. Create the GitHub repository and invite the team.
2. Create a Neo4j AuraDB Free instance and save the URI and password.
3. Create an OpenAI API key.
4. Place the Excel file and P180.pdf in `data/`.

**Tasks for Claude Code:**

1. Create the folder structure from section 2 and a `requirements.txt`.
2. Add `.gitignore` covering secrets, virtual environments and caches, and a `secrets.toml.example` showing the required keys without values.
3. Write `backend/graph.py` with a reusable Neo4j connection read from Streamlit secrets.
4. Write a minimal `app.py` that shows a title and a connection status for Neo4j and OpenAI.
5. Write a README explaining local setup in a few steps.

**Done when:**

- A teammate can clone the repository, add their own secrets file and see both connections reported as working.
- No secret appears anywhere in the repository history.

## 5. Sprint 1: Knowledge Graph

**Goal:** the Excel model is loaded into Neo4j exactly, and can be reloaded at any time without duplicates.

**Tasks for Claude Code:**

1. Write `scripts/load_graph.py`, which reads the Elements and Relationships sheets and creates the nodes and relationships described in section 3.
2. Make the loader validate the Excel before writing: every From and To name exists in Elements, names are unique, and Type and Status contain only allowed values. Stop with a clear message if any check fails.
3. Clear the graph before each load and add a uniqueness constraint on element names.
4. After loading, compare node and relationship counts per type with the Excel and print a short report.
5. Add tests that load the Excel and check the counts and a few known facts, for example that Booking System realizes Create Reservation and that Product Maintenance Application belongs to both Product Management & Design and Maintenance.

**Done when:**

- The graph holds 342 elements and 473 relationships, matching the Excel by type.
- Running the loader twice gives the same result.
- A teammate can browse the graph in the Neo4j Browser and find any element from the Excel.

## 6. Sprint 2: Evidence Layer (RAG)

**Goal:** every element is linked to the P180 pages that support it, and relevant P180 text can be found by meaning.

**Tasks for Claude Code:**

1. Write `scripts/load_evidence.py`, which extracts the text of each P180 page and stores it as an Evidence node with its printed page number and section heading.
2. Note the page offset: the Excel cites printed page numbers, which are 10 lower than the PDF page index (printed page 59 is PDF page 69). Store the printed number.
3. Create an OpenAI embedding for each page and a Neo4j vector index on the embeddings.
4. Read the page numbers from each element’s Source column (for example “P180 Fig. 37, p. 59”) and link the element to those Evidence nodes with SUPPORTED\_BY. Do the same for relationships by storing their page numbers as a property.
5. Write `backend/evidence.py` with two functions: fetch the pages linked to a set of elements, and find the most relevant pages for a free-text question using the vector index.
6. Make the loader safe to rerun, and skip re-embedding pages that are already stored.

**Done when:**

- Every element with a Source value has at least one SUPPORTED\_BY link.
- Searching for “baggage tracking” returns the Ground Operations pages among the top results.
- Evidence text shown to users is short, with a page reference, rather than whole pages.

## 7. Sprint 3: Impact Analysis Engine

**Goal:** for any application, a fixed set of graph queries returns its impact as structured data, with no AI involved.

**Tasks for Claude Code:**

1. Write `backend/impact.py` with a function that takes an application name and returns one structured result containing the findings below.
2. Implement name resolution: an exact match is used directly; otherwise return the closest matching application names so the user can choose. Never guess.
3. Give every finding a label, assigned by the code rather than the AI:

| Finding | Label |
| --- | --- |
| Capabilities the application realizes | Documented fact |
| Capabilities left uncovered, because no other application realizes them | Documented fact |
| Capabilities still covered by another application, and by which one | Documented fact |
| Subdomain and domain the application belongs to | Documented fact |
| Applications and components on the same platform | Inference |
| Other domains that share data objects or platforms with this application’s domain | Inference |
| Dependencies through DEPENDS\_ON links, when any exist | Same as the link’s Status |

4. Include in each finding the graph path that produced it and the source pages, so the answer can be traced.
5. Report explicitly when something is missing, for example that P180 documents no application dependencies. Missing data must never be presented as “no impact”.
6. Return a small graph (nodes and edges with their labels) for the mind map in Sprint 5.
7. Add tests for at least five applications, including Booking System, Direct Sales Platform (shared capability), Product Maintenance Application (two domains) and Notifying Service (no platform).

**Done when:**

- The same application name always returns the same result.
- Every finding carries a label, a path and a source.
- The tests pass.

## 8. Sprint 4: AI Layer

**Goal:** the LLM turns a plain-language question into a request for the engine, and turns the engine’s result into a readable, labelled explanation.

**Tasks for Claude Code:**

1. In `backend/llm.py`, write a parsing step that converts a question into a validated request (operation and target name) using structured output and a Pydantic model. The first supported operation is “replace application”.
2. If the question names no application, an unknown one or an ambiguous one, return a clarification request instead of running the analysis.
3. Write an explanation step that receives only the engine result and the evidence passages, and writes the answer with three visibly labelled parts: documented facts, inferences and recommendations. Recommendations must be phrased as suggestions for an architect to validate.
4. Instruct the model to use only the data it receives, to cite page numbers, and to state plainly what P180 does not document. The LLM never queries Neo4j directly.
5. Add a check after generation: every application, capability and domain named in the answer must exist in the engine result. If not, regenerate once, then fall back to a plain structured summary.
6. Write one function that runs the full flow from question to answer and returns the answer, the evidence and the mind map data together.

**Done when:**

- Questions phrased in different ways about the same application produce the same set of findings.
- No answer names an element that is not in the engine result.
- Ambiguous questions lead to a clarification, not a guess.

## 9. Sprint 5: Streamlit App and Deployment

**Goal:** a deployed web app where an architect asks a question and sees the answer, the evidence and an interactive mind map.

**Tasks for Claude Code:**

1. Build `app.py` with a chat input and chat history.
2. Show each answer in three clearly separated, labelled sections: documented facts, inferences and recommendations.
3. Show the cited P180 evidence below the answer, with page numbers and short excerpts that can be expanded.
4. Draw the mind map with streamlit-agraph: the replaced application in the centre, its capabilities, platform, subdomain and domain around it, and connected domains further out. Use solid lines for documented links and dashed lines for inferred links, so the difference is visible in black and white. Show uncovered capabilities with a distinct marker.
5. Show clarification questions as clickable choices when a name is ambiguous.
6. Add a short sidebar explaining the three labels and that P180 is the only source.
7. Show loading states, and friendly messages when Neo4j or OpenAI cannot be reached, including a hint that the AuraDB instance may be paused.

**Done by the team:**

1. Connect the repository to Streamlit Community Cloud and paste the secrets into its settings.

**Done when:**

- The app works from its public link, end to end, for any application in the model.
- A first-time user can understand an answer without help.

## 10. Sprint 6: Evaluation and Demo

**Goal:** measured results for the evaluation report, and a reliable live demo.

**Done by the team:**

1. Write 15 to 20 test questions with expected answers, worked out by hand from the Excel. Cover several domains and include: an application with a shared capability (Booking System), one in two domains (Product Maintenance Application), one with no platform (Notifying Service), an ambiguous name (“the booking tool”), an unknown name, and a question that is not about replacing an application.
2. Store them in `eval/` as a simple table: question, expected application, expected capabilities, expected uncovered capabilities, expected behaviour.

**Tasks for Claude Code:**

1. Write an evaluation script that runs every test question through the full flow and compares the result with the expected answer.
2. Measure: share of expected capabilities found, share of returned items that are correct, whether every statement is labelled, whether every fact has a page reference, and whether ambiguous and unknown names were handled correctly.
3. Save the results as a table the team can paste into the evaluation report.

**Done when:**

- The evaluation runs with one command and produces the results table.
- The team has rehearsed the demo with three prepared questions, and knows to open the app and resume AuraDB beforehand.

## 11. Build Rules for Claude Code

These rules apply to every sprint:

1. Build one sprint at a time and stop when its “Done when” checks pass. Do not start later sprints early.
2. The Excel file is the only source of architecture facts. Never add, rename or infer elements or relationships in code. Changes to the model are made in the Excel by the team and reloaded.
3. Labels (documented fact, inference, recommendation) are decided by code from each relationship’s Status, never by the LLM.
4. The LLM only parses questions and writes explanations from data it is given. It never connects to Neo4j or runs its own queries.
5. Only predefined queries in `backend/impact.py` run against the database; user text is always passed as a parameter, never inserted into a query.
6. Keep it simple: no agent frameworks, no extra databases, no Docker and no features outside this plan.
7. Keep secrets out of the repository.

**Out of scope for this proof of concept:** editing the model in the app, user accounts, questions other than replacing an application, and any data outside P180.
