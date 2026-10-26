/**
 * Reliability presentation rules, kept as pure functions.
 *
 * These exist separately from the components because they are the UI half
 * of the Stage 8 fix (KNOWN_DEFECTS sections 9 and 10): a score produced
 * while a lookup service was unreachable must never be rendered as an
 * ordinary result. Keeping the decision pure means it can be tested
 * directly and reused everywhere a score is shown - which is the whole
 * requirement, since the dangerous case is a reviewer glancing at the
 * ranked LIST and taking a number at face value.
 */

export function isUnreliable(candidate) {
  // Default to reliable only when the field is explicitly true. An older
  // record, or a backend that forgot to send it, must not be silently
  // presented as trustworthy.
  return candidate?.scoring_reliable === false;
}

export function reliabilityLabel(candidate) {
  return isUnreliable(candidate) ? "SCORE UNRELIABLE" : "";
}

export function reliabilityReasons(candidate) {
  const reasons = candidate?.unreliable_reasons;
  return Array.isArray(reasons) ? reasons : [];
}

/** Candidates in a list whose scores cannot be trusted. */
export function unreliableIn(candidates) {
  return (candidates || []).filter(isUnreliable);
}

/**
 * One sentence for the banner above the whole table.
 * Returns "" when everything is fine, so the caller can render nothing.
 */
export function listWarning(payload) {
  const all = [...(payload?.eligible || []), ...(payload?.gated || [])];
  const affected = unreliableIn(all);
  if (affected.length === 0) return "";
  const names = affected.map((c) => c.candidate_id).join(", ");
  return (
    `${affected.length} of ${all.length} scores could not be fully ` +
    `verified (${names}). A lookup service was unreachable during ` +
    `scoring, so these totals understate the candidates and the ranking ` +
    `below is not trustworthy. Re-run once the service is reachable.`
  );
}

/** Formats a score, never hiding that an override changed it. */
export function scoreDisplay(candidate) {
  const computed = candidate?.computed_total;
  const effective = candidate?.effective_total;
  const overridden =
    typeof computed === "number" &&
    typeof effective === "number" &&
    computed !== effective;
  return { computed, effective, overridden };
}
