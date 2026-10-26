import { useState } from "react";
import { isUnreliable, reliabilityReasons } from "./reliability.js";
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
                <span className="badge">insufficient evidence</span>
              )}
              <div className="muted">{criterion.note}</div>
              {criterion.override && (
                <div className="small">
                  {criterion.override.original_value} →{" "}
                  {criterion.override.new_value} by{" "}
                  {criterion.override.overridden_by}: “
                  {criterion.override.reason}”
                </div>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
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
