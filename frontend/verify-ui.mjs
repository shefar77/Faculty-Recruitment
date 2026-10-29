/**
 * Render the components to static HTML and assert what a reviewer would
 * actually see.
 *
 * Uses react-dom/server plus the esbuild that Vite already installs, so
 * no test framework is added to the project. The point is to prove the
 * reliability flag REACHES THE SCREEN, not merely that a function
 * returns true - that is the UI half of the Stage 8 fix, and a claim
 * about it should be tested rather than asserted.
 *
 *   npm run verify
 */
import { build } from "esbuild";
import { readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { sep } from "node:path";
import { join } from "node:path";

const entry = join(tmpdir(), "verify-entry.jsx");
const bundle = join(tmpdir(), "verify-bundle.cjs");

// Absolute, forward-slashed: the entry lives in the temp directory, so a
// relative import would resolve against tmp rather than the project.
const SRC = join(process.cwd(), "src").split(sep).join("/");

const ENTRY_SOURCE = `
import { renderToStaticMarkup } from "react-dom/server";
import CandidateList from "${SRC}/CandidateList.jsx";
import CandidateDetail from "${SRC}/CandidateDetail.jsx";

globalThis.renderList = (payload) =>
  renderToStaticMarkup(<CandidateList payload={payload} selected={null} onSelect={() => {}} />);
globalThis.renderDetail = (candidate) =>
  renderToStaticMarkup(<CandidateDetail candidate={candidate} onOverridden={() => {}} />);
`;

import { writeFileSync } from "node:fs";
writeFileSync(entry, ENTRY_SOURCE);

await build({
  entryPoints: [entry],
  bundle: true,
  outfile: bundle,
  platform: "node",
  format: "cjs",
  jsx: "automatic",
  absWorkingDir: process.cwd(),
  logLevel: "error",
  // Leave React to Node's own resolution. Bundling it produced TWO
  // React copies, so elements built by one were unrecognisable to the
  // other and surfaced as "Objects are not valid as a React child".
  packages: "external",
});

const { createRequire } = await import("node:module");
createRequire(import.meta.url)(bundle);

// --------------------------------------------------------------------------

let failures = 0;
function check(name, condition, detail = "") {
  if (condition) {
    console.log(`  PASS  ${name}`);
  } else {
    failures += 1;
    console.log(`  FAIL  ${name}${detail ? "\n          " + detail : ""}`);
  }
}

const criterion = (over = {}) => ({
  criterion: "publications", weight: 40, measured: 19, unit: "credited",
  computed_points: 0, effective_points: 0, overridden: false, override: null,
  insufficient_evidence: false, note: "lookup failed", indeterminate: false,
  indeterminate_entries: 0, ...over,
});

const reliable = {
  candidate_id: "Candidate_04", candidate_name: "Dr. Shiva Dixit",
  computed_total: 85.7, effective_total: 85.7,
  computed_eligibility: "met", effective_eligibility: "met",
  scoring_reliable: true, unreliable_reasons: [],
  criteria: [criterion({ computed_points: 38, effective_points: 38 })],
  publications: [], reviewer_flags: [], override_history: [],
  eligibility: { reason: "doctorate awarded 2021" },
};

const unreliable = {
  ...reliable, candidate_id: "Candidate_99", candidate_name: "Outage Case",
  computed_total: 42.7, effective_total: 42.7,
  scoring_reliable: false,
  unreliable_reasons: [
    "publications: 19 entr(ies) could not be verified because the lookup service was unreachable",
  ],
  criteria: [criterion({ indeterminate: true, indeterminate_entries: 19 })],
};

console.log("\nRANKED LIST — the dangerous surface: a number read at a glance");
const listHtml = globalThis.renderList({
  eligible: [reliable, unreliable], gated: [],
});
check("unreliable row carries a visible badge in the LIST",
  listHtml.includes("UNRELIABLE"));
check("unreliable row is visually distinguished (row-unreliable class)",
  listHtml.includes("row-unreliable"));
check("a banner appears above the table",
  listHtml.includes("Scores on this page are not fully verified"));
check("the banner names the affected candidate",
  listHtml.includes("Candidate_99"));
check("the banner says the ranking is not trustworthy",
  listHtml.includes("not trustworthy"));
check("both scores are still shown (computed and effective)",
  listHtml.includes("85.70") && listHtml.includes("42.70"));

console.log("\nRANKED LIST — a healthy run must stay quiet");
const cleanHtml = globalThis.renderList({ eligible: [reliable], gated: [] });
check("no badge when every score is reliable",
  !cleanHtml.includes("UNRELIABLE"));
check("no banner when every score is reliable",
  !cleanHtml.includes("not fully verified"));

console.log("\nRANKED LIST — grouping");
// "Cannot tell" and "does not qualify" shared one heading reading
// "Needs review / not eligible", so a candidate whose doctorate was
// merely unconfirmed appeared to have been judged ineligible. These
// checks pin the two apart.
const uncertain = { ...reliable, candidate_id: "Uncertain_High",
  candidate_name: "Thesis Submitted", computed_total: 99, effective_total: 99,
  computed_eligibility: "indeterminate", effective_eligibility: "indeterminate" };
const failing = { ...reliable, candidate_id: "Failing_Case",
  candidate_name: "No Doctorate", computed_total: 31, effective_total: 31,
  computed_eligibility: "not_met", effective_eligibility: "not_met" };

const groupedHtml = globalThis.renderList({
  eligible: [reliable], needs_review: [uncertain], not_eligible: [failing],
  gated: [uncertain, failing],
});
check("an uncertain candidate gets their own heading",
  groupedHtml.includes("Needs your review"));
check("a failing candidate is NOT filed under that same heading",
  groupedHtml.includes("Does not meet the requirement"));
check("the uncertain group says it is a gap in evidence, not a finding",
  groupedHtml.includes("not a finding against the"));
check("the failing group still warns a high score is not appointability",
  groupedHtml.includes("does not make a candidate appointable"));
check("both candidates are rendered",
  groupedHtml.includes("Uncertain_High") && groupedHtml.includes("Failing_Case"));
check("raw eligibility codes never reach the reviewer",
  !groupedHtml.includes(">indeterminate<") && !groupedHtml.includes(">not_met<"),
  "the database's words are not the reviewer's words");
check("eligibility is shown in plain language instead",
  groupedHtml.includes("Needs review") &&
  groupedHtml.includes("Does not meet requirement"));

const keyHtml = globalThis.renderList({
  eligible: [reliable], needs_review: [], not_eligible: [], gated: [],
  eligibility_explained: {
    plain_requirement: "A candidate must hold an awarded doctorate, or have passed NET.",
    independent_of_score: "Eligibility is decided on its own and never from the score.",
    outcomes: {
      met: { label: "Eligible", meaning: "m1", what_to_do: "w1" },
      indeterminate: { label: "Needs your review", meaning: "m2", what_to_do: "w2" },
      not_met: { label: "Does not meet the requirement", meaning: "m3", what_to_do: "w3" },
    },
    override: "Every outcome can be overridden with a written reason.",
    needs_faculty_approval: ["Whether NET alone suffices for this post."],
    disclaimer: "DEMO/RESEARCH gate.",
  },
});
check("the requirement is stated above the table in plain language",
  keyHtml.includes("must hold an awarded doctorate"));
check("the key says eligibility does not come from the score",
  keyHtml.includes("never from the score"));
check("a stale backend without the split still renders its candidates",
  globalThis.renderList({ eligible: [], gated: [failing] })
    .includes("Failing_Case"),
  "falling back to the old single bucket beats showing an empty page");

console.log("\nDETAIL VIEW");
const detailHtml = globalThis.renderDetail(unreliable);
check("warning is rendered on the detail view",
  detailHtml.includes("This score is not reliable"));
check("the specific reason is shown, not just a generic message",
  detailHtml.includes("could not be verified"));
check("the affected criterion is marked NOT VERIFIED",
  detailHtml.includes("NOT VERIFIED"));
check("warning appears BEFORE the totals in document order",
  detailHtml.indexOf("not reliable") < detailHtml.indexOf("Computed (pipeline)"),
  "a warning below the numbers is a warning the reviewer reads second");

const overridden = {
  ...reliable,
  effective_total: 95.7,
  criteria: [criterion({
    computed_points: 38, effective_points: 48, overridden: true,
    override: { original_value: 38, new_value: 48, reason: "Two more papers confirmed.",
                overridden_by: "Prof. Reviewer", created_at: "2026-10-15T00:00:00Z" },
  })],
  override_history: [{ id: 1, field: "publications", original_value: 38,
    new_value: 48, reason: "Two more papers confirmed.",
    overridden_by: "Prof. Reviewer", created_at: "2026-10-15T00:00:00Z" }],
};
const overrideHtml = globalThis.renderDetail(overridden);
check("computed and effective are both shown when overridden",
  overrideHtml.includes("85.70") && overrideHtml.includes("95.70"));
check("the override is labelled as such",
  overrideHtml.includes("overridden"));
check("the override reason and author appear",
  overrideHtml.includes("Prof. Reviewer") &&
  overrideHtml.includes("Two more papers confirmed."));

// ---------------------------------------------------------------------------
// EXPLANATIONS
//
// Regression guard for a measured defect: the pipeline spent 47 seconds per
// CV - 27% of its total runtime - generating prose that this component then
// dropped on the floor. Nothing failed; the work was simply invisible. A
// test that only checks scores would never have caught it, so the rendering
// of the explanation is pinned here.
// ---------------------------------------------------------------------------
console.log("\nEXPLANATIONS");

const explained = {
  ...reliable,
  explanation_state: { criteria_total: 1, llm_written: 1,
                       template_written: 0, all_llm: true },
  criteria: [criterion({
    computed_points: 38, effective_points: 38,
    explanation: "Publications scored 38 of 40 from 19 corroborated entries.",
    explanation_source: "llm",
  })],
};
const explainedHtml = globalThis.renderDetail(explained);
check("a generated explanation is actually rendered",
  explainedHtml.includes(
    "Publications scored 38 of 40 from 19 corroborated entries."),
  "the pipeline generated prose and the UI discarded it - the original defect");
check("the explanation says the model wrote it",
  explainedHtml.includes("written by the local model"));
check("no generate button once every criterion has prose",
  !explainedHtml.includes("Generate written explanations"));

const templated = {
  ...explained,
  explanation_state: { criteria_total: 1, llm_written: 0,
                       template_written: 1, all_llm: false },
  criteria: [criterion({
    computed_points: 38, effective_points: 38,
    explanation: "The publications criterion scored 38 out of 40.",
    explanation_source: "template",
  })],
};
const templatedHtml = globalThis.renderDetail(templated);
check("a template explanation is rendered too",
  templatedHtml.includes("The publications criterion scored 38 out of 40."));
check("a template is labelled as deterministic, not as model prose",
  templatedHtml.includes("deterministic template") &&
  !templatedHtml.includes("written by the local model"));
check("prose can be requested when it has not been generated",
  templatedHtml.includes("Generate written explanations"));
check("the offer states that generating prose changes no score",
  templatedHtml.includes("changes") && templatedHtml.includes("no score"));

// ---------------------------------------------------------------------------
// PROFESSOR-FACING EVIDENCE
//
// The reviewer is a professor assessing a person, not a developer reading
// a schema. Internal field names leaked into this view through the
// criterion note; these checks pin down that the roles, dates and
// durations are shown instead, and that a gap in our reading of the CV is
// presented as a gap rather than as an absence of experience.
// ---------------------------------------------------------------------------
console.log("\nPROFESSOR-FACING EVIDENCE");

const withEvidence = {
  ...reliable,
  criteria: [criterion({
    criterion: "teaching_experience", weight: 25,
    computed_points: 10, effective_points: 10, measured: 2.0,
    note: "full marks at 5.0 years",
    evidence: [{
      title: "Assistant Professor", organisation: "A College",
      from: "2024-10", to: "present", months: 24, kind: "appointment",
    }, {
      title: "Teaching Assistant", organisation: "A University",
      from: "2022-01", to: "2023-01", months: 12, kind: "assistantship",
    }],
  })],
};
const evidenceHtml = globalThis.renderDetail(withEvidence);
check("the roles behind a score are listed",
  evidenceHtml.includes("Assistant Professor") &&
  evidenceHtml.includes("A College"));
check("dates and durations are shown",
  evidenceHtml.includes("2024-10") && evidenceHtml.includes("24 months"));
check("an assistantship is labelled as half rate",
  evidenceHtml.includes("half rate"));
check("no internal field names reach the reviewer",
  !evidenceHtml.includes("positions[]") &&
  !evidenceHtml.includes("start_date") &&
  !evidenceHtml.includes("source_fields"));

const withGap = {
  ...reliable,
  criteria: [criterion({
    criterion: "industry_experience", weight: 10,
    computed_points: 0, effective_points: 0, measured: 0,
    unscored_positions: [{
      title: "Summer Intern", organisation: "Veltrix",
      from: "2022-07", to: "2022-08",
      reason: "employer name carries no academic or commercial marker",
    }],
  })],
};
const gapHtml = globalThis.renderDetail(withGap);
check("a role that could not be categorised is shown, not hidden",
  gapHtml.includes("Summer Intern") && gapHtml.includes("Veltrix"));
check("the gap is framed as unread evidence, not absent experience",
  gapHtml.includes("not an") && gapHtml.includes("absence of experience"));
check("a criterion needing a decision is badged for the reviewer",
  gapHtml.includes("needs your review"));

const withPatents = {
  ...reliable,
  criteria: [criterion({
    criterion: "publications", weight: 40,
    computed_points: 4, effective_points: 4, measured: 2.0,
    evidence: [
      { title: "A Paper", credit: 1.0, reason: "completed + matched" },
      { kind: "patent", title: "A Method for X", number: "IN2024001",
        credit: 0.75, reason: "granted patent, credited at 0.75" },
    ],
  })],
};
const patentHtml = globalThis.renderDetail(withPatents);
check("patents are listed separately from papers",
  patentHtml.includes("A Method for X") && patentHtml.includes("IN2024001"));
check("the UI states patents were not checked against a register",
  patentHtml.includes("not checked against any patent register"));

// The audit record's evidence list is not uniform: some criteria record a
// schema field rather than a role or a paper (doctorate stores
// {field: "phd_status"}). Now that the API serves the audit evidence
// verbatim, those rows reach the browser, and a professor must not be
// shown an internal field name. The component filters on shape, and that
// filter is what this pins.
const withInternalRow = {
  ...reliable,
  criteria: [criterion({
    criterion: "doctorate", weight: 20,
    computed_points: 0, effective_points: 0, measured: 0,
    evidence: [{ field: "phd_status", evidence: "" }],
  })],
};
const internalHtml = globalThis.renderDetail(withInternalRow);
check("an internal field name in the evidence is not rendered",
  !internalHtml.includes("phd_status"),
  "the audit record legitimately stores field names; the reviewer's view must not show them");

// ---------------------------------------------------------------------------
// REVISED RUBRIC - categories and the factors behind an education mark
//
// The 100 marks now divide into three approved categories, and an
// education mark is the product of three named multipliers. Both have to
// REACH THE SCREEN: a score the reviewer cannot decompose is a score they
// cannot contest, which is the whole point of the rubric being explicit.
// ---------------------------------------------------------------------------
console.log("\nREVISED RUBRIC — CATEGORIES AND FACTORS");

const withCategories = {
  ...reliable,
  category_scores: {
    research: { earned: 21.5, available: 40 },
    experience: { earned: 12.0, available: 25 },
    education: { earned: 24.3, available: 35 },
  },
  criteria: [criterion({
    criterion: "masters", weight: 8, category: "education",
    computed_points: 4.76, effective_points: 4.76, measured: 0.595,
    factors: [
      { factor: "equivalence", value: 0.85,
        reason: "MCA counts as a computing master's" },
      { factor: "institution", value: 0.85,
        reason: "recognised as Central University" },
      { factor: "branch", value: 0.8,
        reason: "the subject is ECE, a neighbouring field" },
    ],
    needs_review: false, review_reasons: [],
  })],
};
const categoryHtml = globalThis.renderDetail(withCategories);
check("the three approved categories are shown with their subtotals",
  categoryHtml.includes("Research and Related Contributions") &&
  categoryHtml.includes("Teaching and Industry Experience") &&
  categoryHtml.includes("Education"));
check("each category shows earned against available",
  categoryHtml.includes("21.50") && categoryHtml.includes("40") &&
  categoryHtml.includes("24.30") && categoryHtml.includes("35"));
check("every multiplier behind an education mark is named",
  categoryHtml.includes("Qualification match") &&
  categoryHtml.includes("Awarding institution") &&
  categoryHtml.includes("Subject relevance"));
check("a criterion row carries a human label, not a database key",
  categoryHtml.includes("Master&#x27;s Degree") &&
  !categoryHtml.includes(">masters<"),
  "the score breakdown showed bachelors / doctorate / teaching_experience");
check("each factor shows its value and its reason",
  categoryHtml.includes("0.85") &&
  categoryHtml.includes("a neighbouring field"),
  "a reviewer must see WHICH factor to disagree with, not one opaque number");

const needsConfirming = {
  ...reliable,
  criteria: [criterion({
    criterion: "doctorate", weight: 15, category: "education",
    computed_points: 10.5, effective_points: 10.5, measured: 0.7,
    needs_review: true,
    review_reasons: [
      "The awarding institution (Greenfield Institute) is not in the " +
      "approved list, so it was scored at the base rate.",
    ],
  })],
};
const confirmHtml = globalThis.renderDetail(needsConfirming);
check("an unclassified institution is surfaced for confirmation",
  confirmHtml.includes("Greenfield Institute") &&
  confirmHtml.includes("Please confirm"));
check("a criterion awaiting confirmation is badged",
  confirmHtml.includes("needs your review"));

// ---------------------------------------------------------------------------
// PROFILE SUMMARY — orientation above the numbers
//
// Assembled by Python from validated fields, never written by a model.
// It has to reach the screen ABOVE the score cards: a reviewer reads a
// number faster than prose, and the prose is what makes the number mean
// something.
// ---------------------------------------------------------------------------
console.log("\nPROFILE SUMMARY");

const summarised = {
  ...reliable,
  profile_summary: {
    text: "Highest completed qualification is M.Tech in Embedded Systems " +
      "from Mewar University (2016); the candidate has submitted a doctoral " +
      "thesis at BITS Pilani, which is awaiting the award.",
    sentences: ["one", "two"],
    unavailable: [],
    generated_by: "deterministic",
  },
};
const summaryHtml = globalThis.renderDetail(summarised);
check("the summary is rendered",
  summaryHtml.includes("Highest completed qualification is M.Tech"));
check("the summary sits ABOVE the score cards",
  summaryHtml.indexOf("Highest completed qualification")
    < summaryHtml.indexOf("Computed (pipeline)"),
  "prose below the numbers is prose the reviewer reads second");

const thinSummary = globalThis.renderDetail({
  ...reliable,
  profile_summary: {
    text: "The CV did not provide enough information we could read.",
    sentences: [], unavailable: ["no completed degree is recorded"],
    generated_by: "deterministic",
  },
});
check("a gap in the CV is named rather than left blank",
  thinSummary.includes("Not stated in the CV") &&
  thinSummary.includes("no completed degree is recorded"));
check("a candidate with no summary renders nothing rather than an empty box",
  !globalThis.renderDetail(reliable).includes("profile-summary"));

rmSync(entry, { force: true });
rmSync(bundle, { force: true });

console.log(`\n${failures === 0 ? "ALL CHECKS PASSED" : failures + " CHECK(S) FAILED"}`);
process.exit(failures === 0 ? 0 : 1);
