import { useState } from "react";
import {
  isUnreliable,
  reliabilityReasons,
  listWarning,
  scoreDisplay,
} from "./reliability.js";
import { eligibilityLabel } from "./labels.js";

/**
 * The ranked table.
 *
 * Two groups, never interleaved, exactly as the API returns them - a
 * gated candidate scoring above an eligible one must not appear to
 * outrank them.
 *
 * Every row that carries an unverifiable score is flagged IN THE LIST,
 * not only in the detail view. A reviewer must never be able to read a
 * number here without also seeing whether it can be trusted.
 */

function ReliabilityBadge({ candidate }) {
  if (!isUnreliable(candidate)) return null;
  return (
    <span className="badge badge-unreliable" title={reliabilityReasons(candidate).join(" ")}>
      ! UNRELIABLE
    </span>
  );
}

function Row({ candidate, selected, onSelect }) {
  const { computed, effective, overridden } = scoreDisplay(candidate);
  const unreliable = isUnreliable(candidate);
  return (
    <tr
      onClick={() => onSelect(candidate.candidate_id)}
      className={
        (selected ? "row-selected " : "") + (unreliable ? "row-unreliable" : "")
      }
    >
      <td>
        <strong>{candidate.candidate_id}</strong>
        <div className="muted">{candidate.candidate_name || "—"}</div>
      </td>
      <td className="num">{computed?.toFixed(2)}</td>
      <td className="num">
        <strong>{effective?.toFixed(2)}</strong>
        {overridden && <span className="badge badge-override">overridden</span>}
      </td>
      <td>
        {eligibilityLabel(candidate.effective_eligibility)}
        {candidate.effective_eligibility !== candidate.computed_eligibility && (
          <div className="muted">
            pipeline said: {eligibilityLabel(candidate.computed_eligibility)}
          </div>
        )}
      </td>
      <td>
        <ReliabilityBadge candidate={candidate} />
      </td>
    </tr>
  );
}

function Group({ title, caption, candidates, selected, onSelect }) {
  return (
    <section className="group">
      <h3>
        {title} <span className="muted">({candidates.length})</span>
      </h3>
      <p className="muted small">{caption}</p>
      {candidates.length === 0 ? (
        <p className="muted small">None.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Candidate</th>
              <th className="num">Computed</th>
              <th className="num">Effective</th>
              <th>Eligibility</th>
              <th>Score quality</th>
            </tr>
          </thead>
          <tbody>
            {candidates.map((candidate) => (
              <Row
                key={candidate.candidate_id}
                candidate={candidate}
                selected={selected === candidate.candidate_id}
                onSelect={onSelect}
              />
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

/**
 * What the three headings mean, in the reviewer's language.
 *
 * Collapsed by default so it does not crowd the table, and served from
 * the API rather than written here, so the wording a professor reads
 * cannot drift away from the rule the scoring code applies.
 */
function EligibilityKey({ explained }) {
  const [open, setOpen] = useState(false);
  if (!explained) return null;
  const order = ["met", "indeterminate", "not_met"];

  return (
    <div className="alert alert-note eligibility-key">
      {/* Both sentences stay visible. They are the two things a reviewer
          must not have to click for: what the requirement is, and that
          it is not derived from the score. Only the per-group detail is
          collapsed, to keep the table in view. */}
      <p className="small">
        <strong>How eligibility is decided.</strong>{" "}
        {explained.plain_requirement} {explained.independent_of_score}{" "}
        <button type="button" className="linkish" onClick={() => setOpen(!open)}>
          {open ? "Hide details" : "What do these groups mean?"}
        </button>
      </p>
      {open && (
        <div className="small">
          <dl className="keylist">
            {order.map((key) => {
              const entry = explained.outcomes?.[key];
              if (!entry) return null;
              return (
                <div key={key}>
                  <dt>{entry.label}</dt>
                  <dd>
                    {entry.meaning}
                    <div className="muted">
                      <em>What to do:</em> {entry.what_to_do}
                    </div>
                  </dd>
                </div>
              );
            })}
          </dl>
          <p>{explained.override}</p>
          {explained.needs_faculty_approval?.length > 0 && (
            <>
              <p>
                <strong>Rules still awaiting faculty approval.</strong> These
                are proposals, not settled policy:
              </p>
              <ul>
                {explained.needs_faculty_approval.map((item, i) => (
                  <li key={i}>{item}</li>
                ))}
              </ul>
            </>
          )}
          <p className="muted">{explained.disclaimer}</p>
        </div>
      )}
    </div>
  );
}

export default function CandidateList({ payload, selected, onSelect }) {
  if (!payload) return <p className="muted">Loading…</p>;
  const warning = listWarning(payload);

  // Fall back to the old single bucket if an older API is answering, so
  // a stale backend shows the candidates it has rather than an empty page.
  const split = payload.needs_review !== undefined;
  const needsReview = split ? payload.needs_review : payload.gated || [];
  const notEligible = split ? payload.not_eligible : [];

  return (
    <div>
      {/* Above the table, not beside it: the reviewer sees this before
          they can read any number. */}
      {warning && (
        <div className="alert alert-danger" role="alert">
          <strong>Scores on this page are not fully verified.</strong>
          <p>{warning}</p>
        </div>
      )}

      <EligibilityKey explained={payload.eligibility_explained} />

      <Group
        title="Eligible"
        caption={
          "The CV evidences an awarded doctorate, or a NET/SET pass. " +
          "Ranked by effective score."
        }
        candidates={payload.eligible || []}
        selected={selected}
        onSelect={onSelect}
      />
      {/* Kept apart from "does not meet the requirement" below. Nobody in
          this group has been rejected - the evidence simply did not
          settle the question, and a person has to look. */}
      <Group
        title="Needs your review"
        caption={
          "We could not confirm the requirement either way from the CV. " +
          "This is a gap in the evidence, not a finding against the " +
          "candidate. Please check the CV and record a decision."
        }
        candidates={needsReview}
        selected={selected}
        onSelect={onSelect}
      />
      <Group
        title="Does not meet the requirement"
        caption={
          "The CV evidences neither an awarded doctorate nor a NET/SET " +
          "pass. A high score here does not make a candidate appointable; " +
          "you can still override if you know of an exception."
        }
        candidates={notEligible}
        selected={selected}
        onSelect={onSelect}
      />
    </div>
  );
}
