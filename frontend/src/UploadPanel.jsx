import { useState } from "react";
import { uploadCV } from "./api.js";

/**
 * Upload runs the whole pipeline synchronously - minutes, because a local
 * 7B model reads the CV three times. No background-job system: a plain
 * explicit message is adequate at this scale, and the alternative is
 * infrastructure this project does not otherwise need.
 */
export default function UploadPanel({ onUploaded }) {
  const [file, setFile] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState("");

  async function submit(event) {
    event.preventDefault();
    if (!file) return;
    setBusy(true);
    setError("");
    setDone("");
    try {
      const audit = await uploadCV(file);
      setDone(
        `${audit.candidate_id} scored ${audit.scoring.total_score}/100 ` +
        `in ${Math.round(audit.run_seconds)}s.`
      );
      setFile(null);
      event.target.reset?.();
      onUploaded?.(audit.candidate_id);
    } catch (exc) {
      setError(String(exc.message || exc));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="card">
      <label>
        Upload a CV (PDF or DOCX)
        <input
          type="file"
          accept=".pdf,.docx"
          onChange={(e) => setFile(e.target.files?.[0] || null)}
        />
      </label>
      <button type="submit" disabled={!file || busy}>
        {busy ? "Running…" : "Run the pipeline"}
      </button>

      {busy && (
        <div className="alert alert-note" role="status">
          <strong>This takes a few minutes.</strong>
          <p className="small">
            The CV is being read by a local model three times, publications
            are being checked against OpenAlex, and the explanations are
            being written. Nothing is sent to a paid service. Please leave
            this tab open — the page is not frozen.
          </p>
        </div>
      )}
      {error && <div className="alert alert-danger small">{error}</div>}
      {done && <div className="alert alert-ok small">{done}</div>}
    </form>
  );
}
