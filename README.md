# Lufthansa AI Architecture Advisor

A proof of concept for the FH Aachen course IT-Consulting in Practice. An enterprise architect asks a question in plain language, such as "What would be affected if we replaced the Booking System?", and gets an explained answer. Every statement in the answer is labelled and traced back to the P180 reference architecture.

The full build plan is in `data/Sprint Plan Lufthansa AI Architecture Advisor.md`.

## Local setup

1. **Clone the repository** and open a terminal in its folder.
2. **Create a virtual environment and install the dependencies:**
   ```bash
   python -m venv .venv
   .venv\Scripts\activate          # Windows
   source .venv/bin/activate       # macOS / Linux
   pip install -r requirements.txt
   ```
3. **Add your secrets.** Copy `secrets.toml.example` to `.streamlit/secrets.toml` and fill in the Neo4j AuraDB URI, user and password and your OpenAI API key. This file is gitignored. Never commit it.
4. **Run the app:**
   ```bash
   streamlit run app.py
   ```
   The page shows whether Neo4j and OpenAI are connected.

If Neo4j cannot be reached, check the Aura console: AuraDB Free instances pause after a few days without use and have to be resumed by hand.

## Loading the architecture model

```bash
python scripts/load_graph.py
```

The loader checks the Excel first: every From and To name must exist in Elements, names must be unique, and Type and Status must use the allowed values. If anything is wrong it lists every problem and writes nothing. Otherwise it replaces all architecture elements in Neo4j and prints the count per type, compared with the Excel. Running it again gives the same graph.

Changes to the model are made in the Excel and reloaded. Never edit elements or relationships directly in Neo4j.

To look around in the Neo4j Browser (open the instance in the Aura console, then Query):

```cypher
MATCH (a:Application {name: "Booking System"})-[r]-(n) RETURN a, r, n
```

Every node has the label `Element`, a label for its type (`Domain`, `Subdomain`, `Application`, `Capability`, `DataObject`, `Platform`, `TechnologyComponent`) and the properties `name`, `type`, `source`, `notes` and `pages` (the printed P180 pages cited in `source`). Relationships (`BELONGS_TO`, `REALIZES`, `ASSOCIATED_WITH`, `USED_IN`, `DEPENDS_ON`) carry `status`, `source`, `notes` and `pages`.

## Loading the P180 evidence

```bash
python scripts/load_evidence.py
```

This stores each P180 page from printed page 1 onwards as an `Evidence` node (`page`, `pdf_page`, `section`, `text`, `embedding`), creates the vector index `evidence_embedding`, and links every element to the pages its Source cites with `SUPPORTED_BY`. Page numbers are always the printed ones, which are 10 lower than the PDF page (printed page 59 is PDF page 69). Pages that have not changed are not embedded again, so rerunning it is cheap.

Run it once after the first `load_graph.py`. Later reloads of the Excel relink the elements to the stored pages automatically.

## Impact analysis

`backend/impact.py` answers "what is affected if we replace this application?" with fixed graph queries and no AI:

```python
from backend.impact import analyze_replacement
result = analyze_replacement("Booking System")
```

The result has a `status` (`ok`, or `ambiguous` / `not_found` with `candidates` to choose from, since names are never guessed), a list of `findings` and a list of `gaps`, plus `nodes` and `edges` for the mind map. Each finding has a `label` (Documented fact or Inference, decided by the code from the Status of the links behind it), the graph `paths` that produced it and the P180 `pages` it cites. Data that P180 does not contain, such as dependencies between applications, is reported as a gap, never as "no impact".

## AI layer

`backend/llm.py` runs the full flow from question to answer:

```python
from backend.llm import answer_question
response = answer_question("What would be affected if we replaced the Booking System?")
```

1. **Parse:** an OpenAI model extracts the operation and the application name exactly as the user wrote it. If no application is named, or the name is unknown or ambiguous, the response is a clarification (`kind="clarification"`, with `options` to choose from) and no analysis runs. Questions that are not about replacing an application get `kind="unsupported"`.
2. **Analyse:** `backend/impact.py` computes the findings. The LLM never touches Neo4j.
3. **Explain:** a second model call receives only the findings, gaps and short P180 excerpts, and writes statements that each cite the ids of the findings they rest on. The code, not the model, puts each statement under **Documented facts** or **Inferences** based on the labels of what it cites, and adds the page numbers. Recommendations are phrased as suggestions to validate.
4. **Check:** every application, capability, domain, subdomain and platform named in the answer must appear in the analysis result, and every finding must be covered. If not, the answer is regenerated once, then replaced by a plain summary built by the code (`generated_by="fallback"`).

`answer_for_application(name)` skips the parsing step, for example after the user picks an option from a clarification. Both return the answer, the evidence and the mind map data together.

## The app

`streamlit run app.py` opens the chat. Ask what would be affected if an application were replaced, or click one of the example questions. Each answer shows:

- a one-line headline and three labelled sections (**Documented facts**, with what P180 does not document; **Inferences**; **Recommendations**), each statement with its P180 pages
- the cited **P180 evidence**: one expandable excerpt per page
- a **mind map** of the replaced application, its capabilities, subdomain, domain, platform and connected domains. Solid lines are documented links, dashed lines are inferred, and red diamonds marked ⚠ are capabilities that would be left uncovered.

If a name is ambiguous, the app offers the matching applications as buttons. If Neo4j or OpenAI cannot be reached, it says so, including a reminder that an AuraDB Free instance may be paused.

## Deploying to Streamlit Community Cloud

1. Push the repository to GitHub (the secrets file is gitignored and stays local).
2. At [share.streamlit.io](https://share.streamlit.io), choose **Create app**, pick the repository and branch, set the main file to `app.py`, and under **Advanced settings** choose Python 3.12 or newer.
3. In **Advanced settings → Secrets**, paste the contents of your `.streamlit/secrets.toml`.
4. Deploy. The app reads the graph and evidence already loaded into AuraDB, so the loaders do not need to run on Streamlit Cloud.

Before a demo, open the app once and check the Aura console: AuraDB Free pauses after a few days without use, and a sleeping Streamlit app takes a minute to wake up.

## Evaluation

```bash
python eval/run_eval.py
```

This runs every question in `eval/questions.csv` through the full flow (real OpenAI and Neo4j calls, about a minute and a few cents) and writes `eval/results/results.csv` (one row per question) and `eval/results/summary.md` (the measured results as Markdown tables, ready to paste into the evaluation report).

`questions.csv` has one row per question: `question`, `expected_application`, `expected_capabilities` and `expected_uncovered` (lists separated by `;`), and `expected_behaviour`: `answer`, `clarify_with_options` (the expected application must be among the choices offered), `clarify_unknown` (asked for another name, no analysis) or `unsupported`. Expected values are worked out by hand from the Excel, not from the app.

It measures: whether each question got the expected behaviour; recall and precision of capabilities and uncovered capabilities; whether every statement carries a label consistent with what it cites; whether every documented fact has a page reference; whether any answer names an element outside the analysis; whether differently phrased questions about one application give the same findings; and response times.

## Demo checklist

1. **The day before:** open the Aura console and resume the instance if it is paused; open the app's public link once so Streamlit Cloud wakes it up.
2. **Ten minutes before:** open the app, check the sidebar says "Connected to Neo4j", and run one question.
3. **Three prepared questions**, which show the main behaviours:
   - "What would be affected if we replaced the Booking System?": six uncovered capabilities, Create Reservation still covered by Direct Sales Platform, the mind map, and the missing dependencies stated as a gap.
   - "What happens if we replace the booking tool?": the app asks which application is meant instead of guessing; click Booking System.
   - "What would be affected if we replaced the Baggage Tracking System?": the platform link is shown as an inference (dashed line) because P180 names it slightly differently.
4. Each answer takes about 10–15 seconds; talk through the progress steps while it runs.

## Running the tests

```bash
python -m pytest
```

Tests that need Neo4j load the Excel into the database from your secrets file, and are skipped if it cannot be reached. The evidence tests also need `load_evidence.py` to have been run. The search test and the "live" AI tests call the OpenAI API (a full run costs a few cents).

## Project structure

| Path | Purpose |
| --- | --- |
| `data/` | The Excel architecture model and P180.pdf |
| `scripts/` | Loaders that fill Neo4j from the Excel and the PDF |
| `backend/graph.py` | Neo4j connection and shared helpers |
| `backend/` | Impact queries, evidence retrieval and the LLM steps (later sprints) |
| `app.py` | The Streamlit interface |
| `eval/` | Test questions and the evaluation script |
| `tests/` | Automated tests |
