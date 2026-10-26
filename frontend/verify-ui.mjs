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
const groupedHtml = globalThis.renderList({
  eligible: [reliable],
  gated: [{ ...reliable, candidate_id: "Gated_High", computed_total: 99,
            effective_total: 99, computed_eligibility: "indeterminate",
            effective_eligibility: "indeterminate" }],
});
check("gated group is rendered separately",
  groupedHtml.includes("Needs review / not eligible"));
check("gated group warns a high score is not appointability",
  groupedHtml.includes("does not mean the candidate is appointable"));

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

rmSync(entry, { force: true });
rmSync(bundle, { force: true });

console.log(`\n${failures === 0 ? "ALL CHECKS PASSED" : failures + " CHECK(S) FAILED"}`);
process.exit(failures === 0 ? 0 : 1);
