import json
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.research import answer_profile, screen_profiles


PROFILE_FILE = ROOT / "out" / "profiles.jsonl"
EXTERNAL_FILE = ROOT / "out" / "external-observations-test.jsonl"


@st.cache_data
def load_profiles():
    if not PROFILE_FILE.exists():
        return []

    rows = []
    for line in PROFILE_FILE.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


@st.cache_data
def load_external_observations():
    if not EXTERNAL_FILE.exists():
        return []
    return [
        json.loads(line)
        for line in EXTERNAL_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def format_value(value):
    if value is None:
        return "—"

    if isinstance(value, float):
        return f"{value:,.0f}"

    if isinstance(value, int):
        return f"{value:,}"

    if isinstance(value, list):
        return ", ".join(str(item) for item in value)

    if isinstance(value, dict):
        parts = []
        for key, val in value.items():
            if val not in (None, "", [], {}):
                label = key.replace("_", " ").title()
                parts.append(f"{label}: {format_value(val)}")
        return " · ".join(parts)

    return str(value)


def get_fact(facts, claim, default="—"):
    for fact in facts:
        if fact["claim"] == claim:
            return fact["value"]
    return default


def get_external_observations(observations, organisation_number):
    return [
        observation
        for observation in observations
        if str(observation.get("organisation_number")) == str(organisation_number)
    ]


st.set_page_config(
    page_title="Signalpost",
    page_icon="🔎",
    layout="wide",
)

# ---------- HEADER ----------

st.title("🔎 Signalpost")
st.caption("Evidence-backed company intelligence for Norwegian companies")

profiles = load_profiles()
external_observations = load_external_observations()

if not profiles:
    st.error("No company profiles found. Run the Signalpost batch first.")
    st.stop()

st.success(f"Loaded {len(profiles):,} company profiles")

# ---------- NAVIGATION ----------

mode = st.radio(
    "What do you want to do?",
    ["Research a company", "Search companies"],
    horizontal=True,
)

# ============================================================
# COMPANY RESEARCH
# ============================================================

if mode == "Research a company":

    st.subheader("Research a company")

    col1, col2 = st.columns([1, 2])

    with col1:
        org = st.text_input(
            "Organisation number",
            placeholder="e.g. 985589003",
        )

    with col2:
        question = st.text_input(
            "Ask a question",
            value="What do we know about this company?",
        )

    if st.button("Research company", type="primary"):

        row = next(
            (
                profile
                for profile in profiles
                if str(profile.get("organisation_number")) == org.strip()
            ),
            None,
        )

        if row is None:
            st.error(
                f"Organisation number {org} was not found in the dataset."
            )

        else:

            result = answer_profile(row, question)
            facts = result.get("facts", [])

            st.divider()

            # ---------- COMPANY HEADER ----------

            company_name = result["company_name"]
            organisation_number = result["organisation_number"]

            municipality = get_fact(
                facts,
                "Municipality",
            )

            legal_form = get_fact(
                facts,
                "Legal form",
            )

            st.header(company_name)

            st.caption(
                f"Organisation number: {organisation_number}"
            )

            header1, header2, header3 = st.columns(3)

            with header1:
                st.caption("Municipality")
                st.write(municipality)

            with header2:
                st.caption("Legal form")
                st.write(legal_form)

            with header3:
                st.caption("Data source")
                st.write("Official Norwegian registry")

            # ---------- FINANCIALS ----------

            financial_claims = {
                "Revenue",
                "Operating result",
                "Annual result",
                "Assets",
                "Debt",
            }

            financial_facts = [
                fact
                for fact in facts
                if fact["claim"] in financial_claims
            ]

            if financial_facts:

                st.divider()
                st.subheader("Financial overview")

                columns = st.columns(len(financial_facts))

                for column, fact in zip(columns, financial_facts):

                    with column:
                        st.metric(
                            fact["claim"],
                            format_value(fact["value"]),
                        )

                st.caption(
                    "Figures are taken from official annual accounts."
                )

            # ---------- COMPANY INFORMATION ----------

            st.divider()
            st.subheader("Company information")

            information_facts = [
                fact
                for fact in facts
                if fact["claim"] not in financial_claims
                and fact["claim"] not in {
                    "Registered name",
                    "Organisation number",
                    "Legal form",
                    "Municipality",
                }
            ]

            for fact in information_facts:

                claim = fact["claim"]
                value = fact["value"]

                with st.container(border=True):

                    st.markdown(f"**{claim}**")

                    if isinstance(value, dict):

                        for key, val in value.items():

                            if val not in (None, "", [], {}):

                                label = key.replace(
                                    "_",
                                    " ",
                                ).title()

                                st.write(
                                    f"**{label}:** "
                                    f"{format_value(val)}"
                                )

                    else:

                        st.write(format_value(value))

                    with st.expander("View evidence"):

                        st.write(
                            f"**Classification:** "
                            f"{fact['classification']}"
                        )

                        st.write(
                            f"**Source:** "
                            f"{fact['source_url']}"
                        )

                        st.write(
                            f"**Retrieved:** "
                            f"{fact['retrieved_at']}"
                        )

            # ---------- EXTERNAL INTELLIGENCE ----------

            external_company = get_external_observations(
                external_observations,
                organisation_number,
            )

            if external_company:

                st.divider()
                st.subheader("External intelligence")

                workforce = [
                    x for x in external_company
                    if x.get("signal_type") == "workforce_snapshot"
                ]

                social = [
                    x for x in external_company
                    if x.get("signal_type") == "profile_handle"
                ]

                activity = [
                    x for x in external_company
                    if x.get("signal_type") == "public_post"
                ]

                if workforce:
                    st.write("### Workforce")

                    for observation in workforce:
                        metrics = observation.get("metrics") or {}
                        value = metrics.get("workforce_value")
                        measure = metrics.get("measure", "workforce")

                        st.metric(
                            "Latest reported workforce",
                            format_value(value),
                        )

                        st.caption(
                            f"{measure.replace('_', ' ').title()} · "
                            f"effective {observation.get('effective_at', '—')}"
                        )

                        with st.expander("View workforce evidence"):
                            st.write(
                                f"**Evidence:** "
                                f"{observation.get('evidence_span', '—')}"
                            )
                            st.write(
                                f"**Source:** "
                                f"{observation.get('source_url', '—')}"
                            )
                            st.write(
                                f"**Retrieved:** "
                                f"{observation.get('retrieved_at', '—')}"
                            )

                if social:
                    st.write("### Verified social profiles")

                    for observation in social:
                        platform = observation.get("platform", "social")
                        source_url = observation.get("source_url", "—")

                        st.markdown(
                            f"**{platform.title()}** — "
                            f"{source_url}"
                        )

                        with st.expander("View verification"):
                            st.write(
                                f"**Identity verified:** "
                                f"{observation.get('exact_entity', False)}"
                            )
                            st.write(
                                f"**Source:** "
                                f"{observation.get('source_class', '—')}"
                            )
                            st.write(
                                f"**Discovery:** "
                                f"{observation.get('metrics', {}).get('discovery_source_url', '—')}"
                            )

                if activity:
                    st.write("### Company-site activity")

                    for observation in activity:
                        st.markdown(
                            f"**{observation.get('evidence_span', 'Company activity')}**"
                        )
                        st.caption(
                            observation.get("source_url", "—")
                        )

                        with st.expander("View evidence"):
                            st.write(
                                f"**Identity verified:** "
                                f"{observation.get('exact_entity', False)}"
                            )
                            st.write(
                                f"**Retrieved:** "
                                f"{observation.get('retrieved_at', '—')}"
                            )
                            st.write(
                                f"**Interpretation:** "
                                f"{observation.get('metrics', {}).get('interpretation', 'Company-owned activity.')}"
                            )

                if not social and not activity:
                    st.caption(
                        "No additional externally verified social or company-site "
                        "activity signals are available for this company in this run."
                    )

            # ---------- IDENTITY / VERIFICATION ----------

            st.divider()
            st.subheader("Verification")

            verification_col1, verification_col2 = st.columns(2)

            with verification_col1:

                st.write("### ✓ Official registry")

                st.write(
                    "Company identity and registry information "
                    "verified from the official registry."
                )

            with verification_col2:

                website_available = any(
                    fact["claim"] == "Official website"
                    for fact in facts
                )

                if website_available:

                    st.write("### ✓ Website")

                    st.write(
                        "A company website was verified "
                        "against the company identity."
                    )

                else:

                    st.write("### ⚠ Website")

                    st.write(
                        "No registry-linked company website "
                        "could be verified in this run."
                    )

            # ---------- UNCERTAINTY ----------

            if result.get("unsupported_or_uncertain"):

                st.divider()
                st.warning(
                    "Information that could not be verified"
                )

                for item in result["unsupported_or_uncertain"]:
                    st.write(f"• {item}")

            st.divider()

            st.caption(
                "Signalpost returns source-linked facts and "
                "does not invent unavailable information."
            )


# ============================================================
# COMPANY SEARCH
# ============================================================

else:

    st.subheader("Search companies")

    st.write(
        "Describe the companies you are looking for using "
        "supported filters such as location, employees, "
        "revenue, industry, profitability, website, or accounts."
    )

    query = st.text_input(
        "Company search",
        placeholder=(
            "e.g. companies in Oslo with more than 10 employees "
            "and revenue over 1 million"
        ),
    )

    if st.button("Search companies", type="primary"):

        if not query.strip():

            st.warning("Enter a search query.")

        else:

            result = screen_profiles(
                profiles,
                query,
            )

            plan = result["plan"]

            with st.expander(
                "How Signalpost interpreted your query"
            ):

                for filter_item in plan.get("filters", []):

                    st.write(
                        f"**{filter_item['field']}** "
                        f"{filter_item['operator']} "
                        f"**{filter_item['value']}**"
                    )

            if plan.get("unsupported"):

                st.warning(
                    "Some parts of this query are unsupported."
                )

                for item in plan["unsupported"]:
                    st.write(f"• {item}")

            if not plan.get("executable"):

                st.error(
                    "This query could not be executed."
                )

            else:

                st.divider()

                st.subheader(
                    f"{result['result_count']:,} matching companies"
                )

                                # Compact search results table
                table_rows = []

                for company in result["results"]:
                    table_rows.append(
                        {
                            "Company": company["name"],
                            "Organisation": company[
                                "organisation_number"
                            ],
                            "Municipality": company.get(
                                "municipality",
                                "—",
                            ),
                            "Employees": company.get(
                                "employees"
                            ),
                            "Revenue": company.get(
                                "revenue"
                            ),
                            "Annual result": company.get(
                                "annual_result"
                            ),
                        }
                    )

                st.dataframe(
                    table_rows,
                    use_container_width=True,
                    hide_index=True,
                )

                st.caption(
                    "Results are filtered deterministically "
                    "from the collected company profiles."
                )

                st.info(
                    "Select an organisation number above and "
                    "use Research a company to view full "
                    "evidence and company details."
                )