import { useRef, useState } from "react";
import { overrideScore } from "./api.js";
import { criterionLabel } from "./labels.js";
import { useToast } from "./toast.jsx";
import {
  overrideFailed, overrideReasonMissing, overrideRecorded,
} from "./notices.js";

/**
 * Records a human override. The reason is mandatory here as well as in
 * the API, so the reviewer is told before the round trip rather than
 * being handed a 422.
 *
 * Outcomes are toasts. A missing reason is ALSO marked on the field
 * itself, because the toast fades and the reviewer still needs to see
 * which box to fill in.
 */
export default function OverrideForm({ candidate, onDone }) {
  const criteria = candidate.criteria || [];
  const [field, setField] = useState(criteria[0]?.criterion || "eligibility");
  const [value, setValue] = useState("");
  const [reason, setReason] = useState("");
  const [by, setBy] = useState("faculty");
  const [busy, setBusy] = useState(false);
  const [reasonMissing, setReasonMissing] = useState(false);
  const reasonBox = useRef(null);
  const toast = useToast();

  const isEligibility = field === "eligibility";
  const selected = criteria.find((c) => c.criterion === field);

  async function submit(event) {
    event.preventDefault();
    if (busy) return;
    if (!reason.trim()) {
      setReasonMissing(true);
      toast.show(overrideReasonMissing());
      reasonBox.current?.focus();
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
      toast.show(overrideRecorded(result, field));
      onDone?.(result);
    } catch (exc) {
      toast.show(overrideFailed(exc));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="card">
      <div className="formrow">
        <label>
          Field
          {/* The option VALUE stays the internal id - that is what the
              API receives and what the audit log records. Only the text
              a reviewer reads is relabelled. */}
          <select value={field} onChange={(e) => setField(e.target.value)}>
            {criteria.map((c) => (
              <option key={c.criterion} value={c.criterion}>
                {criterionLabel(c.criterion)} (now{" "}
                {c.effective_points?.toFixed(2)} / {c.weight})
              </option>
            ))}
            <option value="eligibility">Eligibility</option>
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
          ref={reasonBox}
          rows="2"
          value={reason}
          aria-invalid={reasonMissing || undefined}
          onChange={(e) => {
            setReason(e.target.value);
            if (e.target.value.trim()) setReasonMissing(false);
          }}
          placeholder="e.g. Award letter sighted; degree conferred 2026-09."
        />
      </label>

      <p className="muted small">
        The computed score is never changed. The override is recorded
        alongside it, takes effect in the ranking, and stays in the log.
      </p>

      <button type="submit" disabled={busy || !value}>
        {busy ? "Recording…" : "Record override"}
      </button>
    </form>
  );
}
