import { useState } from "react";
import { uploadCV } from "./api.js";
import { useToast } from "./toast.jsx";
import {
  checkUploadFile, uploadCompleted, uploadFailed, uploadStarted,
} from "./notices.js";

/**
 * Upload runs the whole pipeline synchronously - minutes, because a local
 * 7B model reads the CV three times. No background-job system: a plain
 * explicit message is adequate at this scale, and the alternative is
 * infrastructure this project does not otherwise need.
 *
 * Outcomes are reported as toasts, under one key, so "processing started"
 * is replaced by its own result rather than stacking beside it. The
 * "this takes a few minutes" panel stays inline: it describes a state
 * that lasts the whole run, and a toast would fade long before it ended.
 */
export default function UploadPanel({ onUploaded }) {
  const toast = useToast();
  const [file, setFile] = useState(null);
  const [busy, setBusy] = useState(false);

  function choose(event) {
    const picked = event.target.files?.[0] || null;
    // Check against the server's own rules the moment a file is picked,
    // so a name with a space is caught now, not after pressing Run.
    const problem = checkUploadFile(picked);
    if (problem) {
      toast.show(problem);
      event.target.value = "";
      setFile(null);
      return;
    }
    setFile(picked);
  }

  async function submit(event) {
    event.preventDefault();
    if (!file || busy) return;
    const problem = checkUploadFile(file);
    if (problem) {
      toast.show(problem);
      return;
    }

    // Captured before the await: React clears currentTarget once the
    // handler returns, and the run takes minutes.
    const form = event.currentTarget;
    const name = file.name;
    const startedAt = Date.now();

    setBusy(true);
    toast.show(uploadStarted(name));
    try {
      const audit = await uploadCV(file);
      setFile(null);
      form.reset();
      toast.show(uploadCompleted(audit));
      await onUploaded?.(audit.candidate_id);
    } catch (exc) {
      toast.show(uploadFailed(exc, name, Date.now() - startedAt));
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
          onChange={choose}
          disabled={busy}
        />
      </label>
      <button type="submit" disabled={!file || busy}>
        {busy ? "Running…" : "Run the pipeline"}
      </button>

      {busy && (
        <div className="alert alert-note" role="status">
          <strong>This takes a few minutes.</strong>
          <p className="small">
            The CV is being read by a local model three times, and its
            publications are being checked against OpenAlex. Nothing is
            sent to a paid service. You can keep using the portal, but
            keep this tab open — you'll get a notification when it's done.
          </p>
        </div>
      )}
    </form>
  );
}
