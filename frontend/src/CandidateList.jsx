import {
  isUnreliable,
  reliabilityReasons,
  listWarning,
  scoreDisplay,
} from "./reliability.js";

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
        {candidate.effective_eligibility}
        {candidate.effective_eligibility !== candidate.computed_eligibility && (
          <div className="muted">computed: {candidate.computed_eligibility}</div>
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

export default function CandidateList({ payload, selected, onSelect }) {
  if (!payload) return <p className="muted">Loading…</p>;
  const warning = listWarning(payload);

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

      <Group
        title="Eligible"
        caption="Meets the demo eligibility gate. Ranked by effective score."
        candidates={payload.eligible || []}
        selected={selected}
        onSelect={onSelect}
      />
      <Group
        title="Needs review / not eligible"
        caption={
          "Shown separately and never mixed into the ranking above: a high " +
          "score here does not mean the candidate is appointable."
        }
        candidates={payload.gated || []}
        selected={selected}
        onSelect={onSelect}
      />
    </div>
  );
}
