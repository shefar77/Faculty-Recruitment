import { useState } from "react";
import { overrideScore } from "./api.js";

/**
 * Records a human override. The reason is mandatory here as well as in
 * the API, so the reviewer is told before the round trip rather than
 * being handed a 422.
 */
export default function OverrideForm({ candidate, onDone }) {
  const criteria = candidate.criteria || [];
  const [field, setField] = useState(criteria[0]?.criterion || "eligibility");
  const [value, setValue] = useState("");
  const [reason, setReason] = useState("");
  const [by, setBy] = useState("faculty");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const isEligibility = field === "eligibility";
  const selected = criteria.find((c) => c.criterion === field);

  async function submit(event) {
    event.preventDefault();
    setError("");
    if (!reason.trim()) {
      setError("A reason is required. An unexplained override cannot be "
             + "told apart from a mistake when it is read months later.");
      return;
    }
    setBusy(true);
    try {
      const result = await overrideScore(candidate.candidate_id, {
        field,
        new_value: isEligibility ? value : Number(value),
        reason: reason.trim(),
        overridden_by: by.trim() || "faculty",
      });
      setValue("");
      setReason("");
      onDone?.(result);
    } catch (exc) {
      setError(String(exc.message || exc));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="card">
      <div className="formrow">
        <label>
          Field
          <select value={field} onChange={(e) => setField(e.target.value)}>
            {criteria.map((c) => (
              <option key={c.criterion} value={c.criterion}>
                {c.criterion.replace(/_/g, " ")} (now{" "}
                {c.effective_points?.toFixed(2)} / {c.weight})
              </option>
            ))}
            <option value="eligibility">eligibility</option>
          </select>
        </label>

        <label>
          New value
          {isEligibility ? (
            <select value={value} onChange={(e) => setValue(e.target.value)}>
              <option value="">choose…</option>
              <option value="met">met</option>
              <option value="not_met">not_met</option>
              <option value="indeterminate">indeterminate</option>
            </select>
          ) : (
            <input
              type="number"
              step="0.01"
              min="0"
              max={selected?.weight}
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder={`0 – ${selected?.weight ?? ""}`}
            />
          )}
        </label>

        <label>
          Your name
          <input value={by} onChange={(e) => setBy(e.target.value)} />
        </label>
      </div>

      <label>
        Reason (required)
        <textarea
          rows="2"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="e.g. Award letter sighted; degree conferred 2026-09."
        />
      </label>

      <p className="muted small">
        The computed score is never changed. The override is recorded
        alongside it, takes effect in the ranking, and stays in the log.
      </p>

      {error && <div className="alert alert-danger small">{error}</div>}
      <button type="submit" disabled={busy || !value}>
        {busy ? "Recording…" : "Record override"}
      </button>
    </form>
  );
}
