import { useState } from "react";
import { isUnreliable, reliabilityReasons } from "./reliability.js";
import { generateExplanations } from "./api.js";
import {
  criterionLabel,
  categoryLabel,
  eligibilityLabel,
  factorLabel,
  verificationLabel,
  exclusionLabel,
} from "./labels.js";
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
      criterion.needs_review ||
      criterion.unscored_positions?.length ||
      criterion.unclassified_roles?.length,
  );
}

/**
 * A few sentences of orientation, above the numbers.
 *
 * Assembled by Python from validated fields, never written by a model,
 * so it cannot state a qualification the record does not hold. It sits
 * above the score cards because a reviewer reads a number faster than
 * they read prose, and the prose is what makes the number mean something.
 */
function ProfileSummary({ summary }) {
  if (!summary?.text) return null;
  return (
    <div className="profile-summary">
      <p>{summary.text}</p>
      {summary.unavailable?.length > 0 && (
        <p className="muted small">
          Not stated in the CV: {summary.unavailable.join("; ")}.
        </p>
      )}
    </div>
  );
}

/**
 * How the 100 marks divide across the three approved categories.
 *
 * A reviewer comparing candidates needs to see WHERE a score came from,
 * not only how big it is: 60 earned on research and 60 earned on
 * education describe two very different people.
 */
function CategoryTotals({ scores }) {
  const names = Object.keys(scores || {});
  if (!names.length) return null;
  return (
    <div className="totals">
      {names.map((name) => (
        <div key={name}>
          <div className="muted small">{categoryLabel(name)}</div>
          <div>
            <strong>{scores[name].earned?.toFixed(2)}</strong>
            <span className="muted"> / {scores[name].available}</span>
          </div>
        </div>
      ))}
    </div>
  );
}

/**
 * The named multipliers behind an education mark.
 *
 * Shown rather than folded into one number because a reviewer who
 * disagrees with the score has to be able to see WHICH factor they
 * disagree with - the qualification, the institution or the subject -
 * and each one is separately overridable.
 */
function Factors({ factors }) {
  if (!factors?.length) return null;
  return (
    <ul className="factors">
      {factors.map((factor, i) => (
        <li key={i}>
          <strong>{factorLabel(factor.factor)}</strong> &times;{factor.value}
          <span className="muted"> — {factor.reason}</span>
        </li>
      ))}
    </ul>
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
            <td>{criterionLabel(criterion.criterion)}</td>
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
              <Factors factors={criterion.factors} />

              {criterion.review_reasons?.length > 0 && (
                <div className="alert alert-note small">
                  <strong>Please confirm.</strong>
                  <ul>
                    {criterion.review_reasons.map((reason, i) => (
                      <li key={i}>{reason}</li>
                    ))}
                  </ul>
                </div>
              )}

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
          <th>External check</th>
          <th>Credit</th>
          <th>Evidence</th>
        </tr>
      </thead>
      <tbody>
        {publications.map((publication, index) => (
          <tr key={index}>
            <td className="small">{publication.title}</td>
            <td className="small muted">{publication.status}</td>
            <td className="small">
              {verificationLabel(publication.outcome)}
              {publication.resolved_by === "doi" && (
                <div className="muted">identified by the DOI in the CV</div>
              )}
              {publication.author_match && (
                <div className="muted">author: {publication.author_match}</div>
              )}
              {publication.doi_note && (
                <div className="muted">{publication.doi_note}</div>
              )}
            </td>
            <td className="small">
              {publication.credit ?? "—"}
              {publication.credit_exclusions?.length > 0 && (
                <div>
                  <span className="badge">
                    {publication.credit_exclusions.map(exclusionLabel).join("; ")}
                  </span>
                </div>
              )}
              {publication.validation_verdict === "needs_review" && (
                <div>
                  <span className="badge">needs your review</span>
                </div>
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

      <ProfileSummary summary={candidate.profile_summary} />

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
            <strong>{eligibilityLabel(candidate.effective_eligibility)}</strong>
            {candidate.effective_eligibility !==
              candidate.computed_eligibility && (
              <div className="muted small">
                pipeline said: {eligibilityLabel(candidate.computed_eligibility)}
              </div>
            )}
          </div>
          <div className="muted small">
            {candidate.eligibility?.reason}
          </div>
        </div>
      </div>

      <h3>Score by category</h3>
      <CategoryTotals scores={candidate.category_scores} />

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
                <strong>{entry.field === "eligibility" ? "Eligibility" : criterionLabel(entry.field)}</strong>: {entry.original_value} →{" "}
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
