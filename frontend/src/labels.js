/**
 * Display names for everything the API identifies by a code.
 *
 * ONE SOURCE, used by every component. The dashboard previously printed
 * `criterion.replace(/_/g, " ")`, which put "bachelors" and "doctorate"
 * in front of a professor - the database's words, not a reader's.
 *
 * THE INTERNAL IDENTIFIERS DO NOT CHANGE. The API, the scoring code, the
 * audit records and the override log all keep their existing keys; this
 * module only decides what a human sees. An override recorded against
 * `teaching_experience` three months ago still resolves, and the label
 * can be reworded without touching a stored record.
 */

const CRITERION = {
  publications: "Research Publications",
  research_visibility: "Research Visibility",
  teaching_experience: "Teaching Experience",
  industry_experience: "Industry Experience",
  doctorate: "PhD",
  postdoctoral: "Postdoctoral Experience",
  masters: "Master's Degree",
  bachelors: "Bachelor's Degree",
};

const CATEGORY = {
  research: "Research and Related Contributions",
  experience: "Teaching and Industry Experience",
  education: "Education",
};

const ELIGIBILITY = {
  met: "Eligible",
  indeterminate: "Needs review",
  not_met: "Does not meet requirement",
};

/** The named multipliers behind an education mark. */
const FACTOR = {
  qualification: "Qualification stage",
  equivalence: "Qualification match",
  institution: "Awarding institution",
  branch: "Subject relevance",
  duration: "Duration",
};

/**
 * What the external check concluded about a publication.
 *
 * Kept as distinct phrases on purpose. "Not in the index" and "the check
 * could not run" were both easy to read as "fake", and neither is: the
 * first is a gap in OpenAlex's coverage, the second an outage.
 */
const VERIFICATION = {
  matched: "Confirmed in the index",
  matched_by_biblio: "Confirmed by journal, volume and page",
  weak_match: "Probable match, title or year differs",
  unconfirmed_author: "Found, but candidate not among the authors",
  not_found: "Not in the index (coverage gap, not a finding)",
  lookup_failed: "Check could not run, needs review",
};

/** Why an entry earned nothing. Mirrors schema.CREDIT_EXCLUSIONS. */
const EXCLUSION = {
  failed_validation: "Title not found in the CV",
  thesis_or_dissertation: "A thesis, already counted by the degree",
  duplicate_publication: "Listed more than once, counted once",
};

/**
 * Last resort for an identifier no table covers.
 *
 * Title-casing a raw key is exactly what this module exists to avoid, so
 * it is a fallback and never the plan: a new criterion should be given a
 * real label here. It beats rendering a bare snake_case key at a
 * reviewer, which is the failure being fixed.
 */
function humanise(key) {
  if (!key) return "";
  return String(key)
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

export const criterionLabel = (key) => CRITERION[key] || humanise(key);
export const categoryLabel = (key) => CATEGORY[key] || humanise(key);
export const eligibilityLabel = (key) => ELIGIBILITY[key] || humanise(key);
export const factorLabel = (key) => FACTOR[key] || humanise(key);
export const verificationLabel = (key) => VERIFICATION[key] || humanise(key);
export const exclusionLabel = (key) => EXCLUSION[key] || humanise(key);

export { CRITERION, CATEGORY, ELIGIBILITY, FACTOR, VERIFICATION, EXCLUSION };
