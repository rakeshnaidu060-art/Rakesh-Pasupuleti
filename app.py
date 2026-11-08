"""Lufthansa AI Architecture Advisor: Streamlit interface."""

import neo4j.exceptions
import openai
import streamlit as st
from streamlit_agraph import Config, Edge, Node, agraph

from backend.graph import check_connection
from backend.impact import INFERENCE
from backend.llm import AdvisorResponse, answer_for_application, parse_question, to_request

st.set_page_config(page_title="AI Architecture Advisor", page_icon=":material/flight:", layout="wide")

EXAMPLES = {
    ":material/search: Replace the Booking System": "What would be affected if we replaced the Booking System?",
    ":material/swap_horiz: Retire the Notifying Service": "What happens if we retire the Notifying Service?",
    ":material/build: Switch off the Product Maintenance Application": "What is the impact of switching off the Product Maintenance Application?",
}

AURA_HINT = (
    "If you use AuraDB Free, the instance may be paused after a few days without use. "
    "Resume it in the Aura console (console.neo4j.io) and try again."
)

# Mind map styling by node role. Shapes differ as well as colours, so the map
# also reads in black and white.
NODE_STYLE = {
    "application": {"shape": "box", "color": "#1f3a93", "font": {"color": "#ffffff", "size": 18}, "size": 30},
    "capability": {"shape": "dot", "color": "#5b8def", "size": 14},
    "uncovered_capability": {"shape": "diamond", "color": "#d64545", "size": 18},
    "covering_application": {"shape": "box", "color": "#9aa5b1", "font": {"color": "#ffffff"}, "size": 18},
    "subdomain": {"shape": "ellipse", "color": "#f2b134"},
    "domain": {"shape": "hexagon", "color": "#e07b00", "size": 24},
    "platform": {"shape": "database", "color": "#4caf8f"},
    "connected_domain": {"shape": "hexagon", "color": "#f6c589", "size": 16},
}
EDGE_TEXT = {"REALIZES": "realizes", "BELONGS_TO": "belongs to", "SHARES_DATA": "shares data"}


# --- State ---

if "messages" not in st.session_state:
    st.session_state.messages = []  # {"role": "user", "content": str} or {"role": "assistant", "response"/"error": ...}
if "pending_application" not in st.session_state:
    st.session_state.pending_application = None


def choose_application(name: str) -> None:
    st.session_state.pending_application = name


def new_conversation() -> None:
    st.session_state.messages = []
    st.session_state.pending_application = None
    st.session_state.pop("example", None)  # so the old suggestion does not fire again


@st.cache_data(ttl=60, show_spinner=False)
def neo4j_status() -> tuple[bool, str]:
    return check_connection()


# --- Rendering ---


def pages_text(pages: list[int]) -> str:
    if not pages:
        return ""
    return f" :gray[(P180 {'p.' if len(pages) == 1 else 'pp.'} {', '.join(map(str, pages))})]"


def render_statements(statements, empty: str) -> None:
    if not statements:
        st.caption(empty)
        return
    st.markdown("\n".join(f"- {s.text}{pages_text(s.pages)}" for s in statements))


def render_mind_map(response: AdvisorResponse, index: int) -> None:
    nodes = [
        Node(
            id=n["id"],
            label=("⚠ " if n["role"] == "uncovered_capability" else "") + n["id"],
            title=f"{n['type']} ({n['role'].replace('_', ' ')})",
            # Plain data field (not a vis option): keeps maps in different
            # messages distinct component instances even when identical.
            message=index,
            **NODE_STYLE[n["role"]],
        )
        for n in response.mind_map["nodes"]
    ]
    edges = [
        Edge(
            source=e["source"],
            target=e["target"],
            label=EDGE_TEXT.get(e["type"], e["type"].lower()),
            title=f"{e['label']}" + (f": {e['title']}" if e["title"] else ""),
            dashes=e["label"] == INFERENCE,
            color="#7b8794",
            font={"size": 10, "align": "middle"},
        )
        for e in response.mind_map["edges"]
    ]
    # 860px fits the chat column of the wide layout on a laptop screen.
    config = Config(width=860, height=600, directed=True, physics=True, groups={})
    # Spread nodes out so labels do not overlap.
    config.physics = {
        "enabled": True,
        "solver": "barnesHut",
        "barnesHut": {"gravitationalConstant": -9000, "springLength": 140, "avoidOverlap": 0.6},
        "stabilization": {"enabled": True, "iterations": 400, "fit": True},
    }
    agraph(nodes=nodes, edges=edges, config=config)
    st.caption(
        "Solid line: documented link in P180. Dashed line: inferred link. "
        "⚠ Diamond: capability left uncovered. Box: application. Hexagon: domain "
        "(pale for connected domains). Ellipse: subdomain. Cylinder: platform."
    )


def render_answer(response: AdvisorResponse, index: int, latest: bool) -> None:
    answer = response.answer
    st.markdown(f"**{answer.headline}**")
    if answer.generated_by == "fallback":
        st.caption(
            ":material/info: The AI explanation did not pass the consistency check, "
            "so this is a plain summary of the analysis."
        )

    with st.container(border=True):
        st.markdown("#### :material/verified: Documented facts")
        st.caption("Shown in P180 and read directly from the architecture model.")
        render_statements(answer.facts, "No documented facts.")
        if answer.not_documented:
            st.markdown("**:material/help: Not documented in P180**")
            render_statements(answer.not_documented, "")

    with st.container(border=True):
        st.markdown("#### :material/lightbulb: Inferences")
        st.caption("Derived from the model, for example through shared platforms or data. Not stated in P180.")
        render_statements(answer.inferences, "No inferences.")

    with st.container(border=True):
        st.markdown("#### :material/assignment: Recommendations")
        st.caption("Suggestions for an architect to validate. Not facts.")
        render_statements(answer.recommendations, "No recommendations.")

    if response.evidence:
        st.markdown(f"**:material/menu_book: P180 evidence** :gray[({len(response.evidence)} pages)]")
        for item in response.evidence:
            section = item["section"].split(" > ")[-1]
            with st.expander(f"p. {item['page']} · {section}"):
                st.markdown(f"> {item['excerpt']}")
                st.caption(f"Linked to: {', '.join(item['elements'])}")

    if st.toggle("Show mind map", value=latest, key=f"show-map-{index}"):
        render_mind_map(response, index)


def render_response(response: AdvisorResponse, index: int, latest: bool) -> None:
    if response.kind == "answer":
        render_answer(response, index, latest)
    elif response.kind == "clarification":
        st.markdown(response.message)
        if response.options:
            with st.container(horizontal=True):
                for option in response.options:
                    st.button(option, key=f"option-{index}-{option}", on_click=choose_application,
                              args=(option,), disabled=not latest, icon=":material/arrow_forward:")
        else:
            st.caption("Name the application as it appears in P180, for example \"Booking System\".")
    else:
        st.markdown(response.message)


def render_error(error: str) -> None:
    st.error(error, icon=":material/error:")


# --- Running a question ---


def friendly_error(exc: Exception) -> str:
    if isinstance(exc, (neo4j.exceptions.ServiceUnavailable, neo4j.exceptions.AuthError, neo4j.exceptions.SessionExpired)):
        return f"The architecture database (Neo4j) cannot be reached. {AURA_HINT}"
    if isinstance(exc, neo4j.exceptions.Neo4jError):
        return f"The architecture database returned an error: {exc.message or exc}"
    if isinstance(exc, openai.AuthenticationError):
        return "The OpenAI API rejected the API key. Check OPENAI_API_KEY in the app secrets."
    if isinstance(exc, openai.RateLimitError):
        return "The OpenAI API is rate limiting requests or the account has no credit left. Try again in a minute."
    if isinstance(exc, (openai.APIConnectionError, openai.APITimeoutError)):
        return "The OpenAI API cannot be reached. Check the internet connection and try again."
    if isinstance(exc, openai.APIError):
        return f"The OpenAI API returned an error: {exc}"
    return f"Something went wrong: {exc}"


def run_question(question: str) -> AdvisorResponse:
    with st.status(":shimmer[Working on it]", type="compact") as status:
        with st.status("Understanding the question", type="step"):
            parsed = parse_question(question)
            st.write(f"Operation: {parsed.operation.replace('_', ' ')}; application: {parsed.application_name or 'none named'}")
        with st.status("Looking up the application in the architecture model", type="step"):
            request, early, result = to_request(parsed, question)
            st.write(f"Found {request.application}." if request else "Needs clarification.")
        if early is not None:
            status.update(label="Done", state="complete")
            return early
        with st.status("Analysing the impact and writing the explanation", type="step"):
            response = answer_for_application(request.application, question, result)
            st.write(f"{len(result.findings)} findings, {len(response.evidence)} evidence pages.")
        status.update(label="Done", state="complete")
    return response


def run_application(name: str) -> AdvisorResponse:
    with st.status(f":shimmer[Analysing {name}]", type="compact") as status:
        response = answer_for_application(name)
        status.update(label="Done", state="complete")
    return response


def handle(question: str, runner, argument: str) -> None:
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        try:
            response = runner(argument)
        except Exception as exc:  # shown to the user, not raised
            error = friendly_error(exc)
            render_error(error)
            st.session_state.messages.append({"role": "assistant", "error": error})
            return
        st.session_state.messages.append({"role": "assistant", "response": response})
        render_response(response, len(st.session_state.messages) - 1, latest=True)


# --- Sidebar ---

with st.sidebar:
    st.header("How to read answers")
    st.markdown(
        "Ask what would be affected if an application were replaced. Every answer has three labelled parts:\n\n"
        "- :material/verified: **Documented facts**: shown in P180 and read from the architecture model.\n"
        "- :material/lightbulb: **Inferences**: derived from the model, for example through a shared platform "
        "or shared data. Possible effects, not certainties.\n"
        "- :material/assignment: **Recommendations**: suggestions for an architect to validate.\n\n"
        ":material/help: **Not documented in P180** lists what the source does not cover. "
        "Missing data never means \"no impact\"."
    )
    st.info(
        "The only source is The Open Group Commercial Aviation Reference Architecture (P180). "
        "Page numbers refer to its printed pages. Labels are assigned by the code, not by the AI.",
        title="Source",
        icon=":material/menu_book:",
    )
    ok, message = neo4j_status()
    if ok:
        st.caption(f":material/check_circle: {message}")
    else:
        st.caption(":material/error: Architecture database unavailable.")
    st.button("New conversation", icon=":material/refresh:", on_click=new_conversation, width="stretch")


# --- Main ---

st.title("AI Architecture Advisor")
st.caption("Impact analysis for replacing an application, based on the P180 reference architecture.")

if not ok:
    st.error(f"The architecture database (Neo4j) cannot be reached. {AURA_HINT}", icon=":material/cloud_off:")
    with st.expander("Details"):
        st.code(message, language=None)

last = len(st.session_state.messages) - 1
for i, msg in enumerate(st.session_state.messages):
    with st.chat_message(msg["role"]):
        if msg["role"] == "user":
            st.markdown(msg["content"])
        elif "error" in msg:
            render_error(msg["error"])
        else:
            render_response(msg["response"], i, latest=i == last)

prompt = st.chat_input("Ask about replacing an application", submit_mode="disable", disabled=not ok)

if not st.session_state.messages and ok:
    suggestions = st.empty()
    selected = suggestions.pills("Try asking:", list(EXAMPLES), label_visibility="collapsed", key="example")
    if selected and not prompt:
        prompt = EXAMPLES[selected]
    if prompt:
        suggestions.empty()

if st.session_state.pending_application:
    name = st.session_state.pending_application
    st.session_state.pending_application = None
    handle(name, run_application, name)
elif prompt:
    handle(prompt, run_question, prompt)
