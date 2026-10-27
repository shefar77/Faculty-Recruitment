import { useState } from "react";
import { isUnreliable, reliabilityReasons } from "./reliability.js";
import { generateExplanations } from "./api.js";
import OverrideForm from "./OverrideForm.jsx";

/** Per-criterion evidence, override history, publication verdicts, flags. */

function Warnings({ candidate }) {
  if (!isUnreliable(candidate)) return null;
  const reasons = reliabilityReasons(candidate);
  return (
    <div className="alert alert-danger" role="alert">
      <strong>This score is not reliable.</strong>
      <ul>
        {reasons.map((reason, index) => (
          <li key={index}>{reason}</li>
        ))}
      </ul>
      <p className="small">
        A lookup service was unreachable while this candidate was scored.
        The figures below understate them and must not be read as a
        judgement on their record. Re-run the pipeline once the service is
        reachable.
      </p>
    </div>
  );
}

/** Does this criterion need a human decision before it can be trusted? */
function needsReview(criterion) {
  return Boolean(
    criterion.indeterminate ||
      criterion.unscored_positions?.length ||
      criterion.unclassified_roles?.length,
  );
}

/**
 * The evidence behind a score, in the reviewer's language.
 *
 * Deliberately NOT the raw note: that string carries internal wording
 * ("UNCLASSIFIED:", field names, saturation thresholds) written for the
 * audit record. A professor needs the roles, the dates and the durations
 * that produced the number, and those are shown directly.
 */
function Evidence({ criterion }) {
  const rows = criterion.evidence || [];
  if (!rows.length) return null;

  const roles = rows.filter((row) => row.months !== undefined);
  const papers = rows.filter((row) => row.credit !== undefined && row.kind !== "patent");
  const patents = rows.filter((row) => row.kind === "patent");

  return (
    <div className="evidence">
      {roles.length > 0 && (
        <ul>
          {roles.map((row, i) => (
            <li key={i}>
              <strong>{row.title}</strong>
              {row.organisation ? ` — ${row.organisation}` : ""}
              {row.from ? ` · ${row.from} to ${row.to}` : ""}
              {row.months ? ` · ${row.months} months` : ""}
              {row.kind === "assistantship" && (
                <span className="muted"> · counted at half rate (assistantship)</span>
              )}
            </li>
          ))}
        </ul>
      )}

      {papers.length > 0 && (
        <ul>
          {papers.map((row, i) => (
            <li key={i}>
              {row.credit > 0 ? "✓" : "—"} {row.title}
              <span className="muted"> · {row.reason}</span>
            </li>
          ))}
        </ul>
      )}

      {patents.length > 0 && (
        <>
          <div className="muted">
            Patents (not checked against any patent register):
          </div>
          <ul>
            {patents.map((row, i) => (
              <li key={i}>
                {row.credit > 0 ? "✓" : "—"} {row.title}
                {row.number ? ` · ${row.number}` : ""}
                <span className="muted"> · {row.reason}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

function Criteria({ criteria }) {
  return (
    <table>
      <thead>
        <tr>
          <th>Criterion</th>
          <th className="num">Computed</th>
          <th className="num">Effective</th>
          <th className="num">Weight</th>
          <th>Measured</th>
          <th>Notes</th>
        </tr>
      </thead>
      <tbody>
        {criteria.map((criterion) => (
          <tr
            key={criterion.criterion}
            className={criterion.indeterminate ? "row-unreliable" : ""}
          >
            <td>{criterion.criterion.replace(/_/g, " ")}</td>
            <td className="num">{criterion.computed_points?.toFixed(2)}</td>
            <td className="num">
              <strong>{criterion.effective_points?.toFixed(2)}</strong>
              {criterion.overridden && (
                <span className="badge badge-override">overridden</span>
              )}
            </td>
            <td className="num muted">{criterion.weight}</td>
            <td className="small">
              {criterion.measured} {criterion.unit}
            </td>
            <td className="small">
              {criterion.indeterminate && (
                <span className="badge badge-unreliable">! NOT VERIFIED</span>
              )}
              {criterion.insufficient_evidence && (
                <span className="badge">no evidence in CV</span>
              )}
              {needsReview(criterion) && (
                <span className="badge badge-review">needs your review</span>
              )}

              <Evidence criterion={criterion} />

              {criterion.unscored_positions?.length > 0 && (
                <div className="alert alert-note small">
                  <strong>Documented roles this score could not count.</strong>{" "}
                  This is a gap in what could be read from the CV, not an
                  absence of experience.
                  <ul>
                    {criterion.unscored_positions.map((role, i) => (
                      <li key={i}>
                        <strong>{role.title}</strong>
                        {role.organisation ? ` at ${role.organisation}` : ""}
                        {role.from ? ` (${role.from}${role.to ? ` to ${role.to}` : ""})` : ""}
                        <div className="muted">{role.reason}</div>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {criterion.unclassified_roles?.length > 0 && (
                <div className="alert alert-note small">
                  {criterion.unclassified_roles.map((note, i) => (
                    <div key={i}>{note}</div>
                  ))}
                </div>
              )}

              {criterion.override && (
                <div className="small">
                  {criterion.override.original_value} →{" "}
                  {criterion.override.new_value} by{" "}
                  {criterion.override.overridden_by}: “
                  {criterion.override.reason}”
                </div>
              )}
              {criterion.explanation && (
                <div className="explanation">
                  {criterion.explanation}
                  <span className="muted"> (
                    {criterion.explanation_source === "llm"
                      ? "written by the local model from the computed figures"
                      : "deterministic template"}
                    )</span>
                </div>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/**
 * Prose is generated on request, not during upload.
 *
 * It is the only pipeline output that cannot change a result - the model
 * is handed the finished criterion, never the CV - so it does not belong
 * on the path a reviewer waits on. The scores below are complete and
 * final whether or not this is ever pressed.
 */
function ExplanationControl({ candidate, onDone }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const state = candidate.explanation_state;
  if (!state || state.all_llm) return null;

  return (
    <div className="alert alert-note">
      <p className="small">
        {state.llm_written === 0
          ? "Each criterion currently carries a deterministic template sentence."
          : `${state.llm_written} of ${state.criteria_total} criteria have written explanations.`}{" "}
        Generating prose takes a minute or two on this machine and changes
        no score.
      </p>
      <button
        type="button"
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          setError(null);
          try {
            await generateExplanations(candidate.candidate_id);
            onDone?.();
          } catch (exception) {
            setError(exception.message);
          } finally {
            setBusy(false);
          }
        }}
      >
        {busy ? "Generating…" : "Generate written explanations"}
      </button>
      {error && <div className="alert alert-danger small">{error}</div>}
    </div>
  );
}

function Publications({ publications }) {
  if (!publications?.length) return <p className="muted small">None listed.</p>;
  return (
    <table>
      <thead>
        <tr>
          <th>Title</th>
          <th>Declared</th>
          <th>Verdict</th>
          <th>Evidence</th>
        </tr>
      </thead>
      <tbody>
        {publications.map((publication, index) => (
          <tr key={index}>
            <td className="small">{publication.title}</td>
            <td className="small muted">{publication.status}</td>
            <td className="small">
              {publication.outcome}
              {publication.author_match && (
                <div className="muted">author: {publication.author_match}</div>
              )}
              {publication.credit_exclusions?.length > 0 && (
                <span className="badge">
                  excluded: {publication.credit_exclusions.join(", ")}
                </span>
              )}
            </td>
            <td className="small">
              {publication.openalex_id ? (
                <a href={publication.openalex_id} target="_blank" rel="noreferrer">
                  OpenAlex record
                </a>
              ) : publication.query_url ? (
                <a href={publication.query_url} target="_blank" rel="noreferrer">
                  search performed
                </a>
              ) : (
                <span className="muted">—</span>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function CandidateDetail({ candidate, onOverridden }) {
  const [lastLogged, setLastLogged] = useState(null);
  if (!candidate) {
    return <p className="muted">Select a candidate to see the evidence.</p>;
  }

  return (
    <div>
      <h2>
        {candidate.candidate_id}{" "}
        <span className="muted">{candidate.candidate_name}</span>
      </h2>

      {/* Immediately under the name, before any number. */}
      <Warnings candidate={candidate} />

      <div className="totals">
        <div>
          <div className="muted small">Computed (pipeline)</div>
          <div className="big">{candidate.computed_total?.toFixed(2)}</div>
        </div>
        <div>
          <div className="muted small">Effective (after overrides)</div>
          <div className="big">{candidate.effective_total?.toFixed(2)}</div>
        </div>
        <div>
          <div className="muted small">Eligibility</div>
          <div>
            <strong>{candidate.effective_eligibility}</strong>
            {candidate.effective_eligibility !==
              candidate.computed_eligibility && (
              <div className="muted small">
                computed: {candidate.computed_eligibility}
              </div>
            )}
          </div>
          <div className="muted small">
            {candidate.eligibility?.reason}
          </div>
        </div>
      </div>

      <h3>Criteria</h3>
      <ExplanationControl candidate={candidate} onDone={onOverridden} />
      <Criteria criteria={candidate.criteria || []} />

      {candidate.reviewer_flags?.length > 0 && (
        <>
          <h3>Reviewer flags</h3>
          <div className="alert alert-note">
            <p className="small muted">
              Advisory only. These affect no score and no eligibility outcome.
            </p>
            <ul>
              {candidate.reviewer_flags.map((flag, index) => (
                <li key={index} className="small">
                  {flag.message}
                </li>
              ))}
            </ul>
          </div>
        </>
      )}

      <h3>Publications</h3>
      <p className="muted small">
        A name matching an author list raises confidence; it does not prove
        authorship. Open the record to judge for yourself.
      </p>
      <Publications publications={candidate.publications} />

      <h3>Override a score</h3>
      <OverrideForm
        candidate={candidate}
        onDone={(result) => {
          setLastLogged(result);
          onOverridden?.();
        }}
      />
      {lastLogged && (
        <div className="alert alert-ok">
          Override #{lastLogged.override_id} logged. Effective total{" "}
          {lastLogged.changed.total[0]?.toFixed(2)} →{" "}
          <strong>{lastLogged.changed.total[1]?.toFixed(2)}</strong>. The
          computed score stays {lastLogged.computed_total?.toFixed(2)}.
        </div>
      )}

      {candidate.override_history?.length > 0 && (
        <>
          <h3>Override history</h3>
          <ul className="small">
            {candidate.override_history.map((entry) => (
              <li key={entry.id}>
                <strong>{entry.field}</strong>: {entry.original_value} →{" "}
                {entry.new_value} — {entry.overridden_by},{" "}
                {entry.created_at}
                <div className="muted">“{entry.reason}”</div>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
