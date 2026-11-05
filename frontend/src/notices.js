/**
 * Every toast the portal can show, written in one place.
 *
 * WHY A CATALOGUE AND NOT INLINE STRINGS
 *   The same failure can surface from several places - an unreachable
 *   server breaks the list, the upload and an override alike. Writing the
 *   wording once keeps it consistent, and keeping these as plain functions
 *   (no React, no timers) lets verify-ui.mjs test exactly what a reviewer
 *   would read.
 *
 * A notice is { kind, title, message, key }.
 *   kind     success | info | warning | error
 *   key      notices that share a key REPLACE one another instead of
 *            stacking. "Processing started" is replaced by its own result,
 *            and five failed requests during one outage are one toast.
 *
 * WARNING VERSUS ERROR
 *   warning  something the user can fix - a wrong file, a missing reason,
 *            a value out of range. Nothing is broken.
 *   error    the system failed - the server, the model, an unexpected
 *            crash. The user did nothing wrong.
 */
import { criterionLabel, eligibilityLabel } from "./labels.js";

export const UPLOAD_KEY = "upload";
export const SERVER_KEY = "server";
export const OVERRIDE_KEY = "override";
export const EXPLAIN_KEY = "explain";
export const LOAD_KEY = "load";

/** Mirrors SAFE_NAME and ALLOWED_SUFFIXES in src/api/main.py. */
const SAFE_NAME = /^[A-Za-z0-9_.-]+$/;
const ACCEPTED_SUFFIXES = [".pdf", ".docx"];

/**
 * Below this, an unreachable upload never reached the server at all. Above
 * it, the server was processing and then went quiet - which may mean the
 * result was saved anyway, so the wording must not claim it was lost.
 */
const LOST_MIDWAY_MS = 10000;
const MAX_DETAIL = 220;

// --------------------------------------------------------------------------
// Helpers
// --------------------------------------------------------------------------

/**
 * Strip the technical scaffolding from a server message.
 *
 * "pipeline failed: RuntimeError: text extraction failed: ..." becomes
 * "text extraction failed: ...". Exception class names mean nothing to a
 * reviewer, and the leading "pipeline failed" is already said by the
 * toast's title.
 */
export function cleanDetail(detail) {
  let text = String(detail || "").trim();
  text = text.replace(/^pipeline failed:\s*/i, "");
  text = text.replace(/^explanation generation failed:\s*/i, "");
  for (let i = 0; i < 3; i += 1) {
    text = text.replace(/^[A-Za-z]+(?:Error|Exception):\s*/, "");
  }
  text = text.replace(/[.\s]+$/, "");
  if (text.length > MAX_DETAIL) text = text.slice(0, MAX_DETAIL - 1).trimEnd() + "…";
  return text;
}

const twoDp = (value) =>
  typeof value === "number" && Number.isFinite(value) ? value.toFixed(2) : "—";

function formatDuration(seconds) {
  if (!seconds || seconds < 60) return "under a minute";
  const minutes = Math.round(seconds / 60);
  return minutes === 1 ? "1 minute" : `${minutes} minutes`;
}

function suffixOf(name) {
  const dot = name.lastIndexOf(".");
  return dot < 0 ? "" : name.slice(dot).toLowerCase();
}

const isModelFailure = (detail) =>
  /extraction failed, refusing to score|ollama|11434|connection|timed out|timeout|cuda|llm|model/i
    .test(detail || "");

function serverUnreachable(consequence) {
  return {
    key: SERVER_KEY,
    kind: "error",
    title: "Can't reach the server",
    message: `${consequence} Check that the backend is running, then try again.`,
  };
}

function serverFault(error, action) {
  return {
    kind: "error",
    title: "Server error",
    message: `Something went wrong on the server while ${action} ` +
      `(error ${error.status}). Try again; if it keeps happening, check ` +
      `the backend log.`,
  };
}

// --------------------------------------------------------------------------
// Upload and processing
// --------------------------------------------------------------------------

/**
 * Check a file BEFORE it is sent, using the server's own rules.
 *
 * Returns null when the file is acceptable. A name with a space is the
 * ordinary case this catches - "John Smith CV.pdf" - and finding out at
 * pick time is far better than after pressing Run.
 */
export function checkUploadFile(file) {
  if (!file) return null;
  const name = file.name || "";
  if (!ACCEPTED_SUFFIXES.includes(suffixOf(name))) {
    return {
      key: UPLOAD_KEY,
      kind: "warning",
      title: "File type not supported",
      message: `${name || "This file"} isn't a PDF or Word (.docx) file. ` +
        `Only those formats can be screened.`,
    };
  }
  if (!SAFE_NAME.test(name)) {
    return {
      key: UPLOAD_KEY,
      kind: "warning",
      title: "Please rename the file",
      message: `"${name}" contains spaces or special characters. Rename it ` +
        `using only letters, numbers, dots, dashes or underscores ` +
        `(for example John_Smith_CV.pdf), then choose it again.`,
    };
  }
  if (file.size === 0) {
    return {
      key: UPLOAD_KEY,
      kind: "warning",
      title: "The file is empty",
      message: `${name} has no content. Choose the CV again, or export a ` +
        `fresh copy.`,
    };
  }
  return null;
}

export function uploadStarted(fileName) {
  return {
    key: UPLOAD_KEY,
    kind: "info",
    title: "CV uploaded — processing started",
    message: `${fileName} is being read and scored. This usually takes ` +
      `several minutes; you can keep working and you'll be notified when ` +
      `it's done.`,
  };
}

export function uploadCompleted(audit) {
  const scoring = audit?.scoring || {};
  const id = audit?.candidate_id || "The candidate";
  const who = scoring.candidate_name ? `${scoring.candidate_name} (${id})` : id;
  const score = `${twoDp(scoring.total_score)}/100`;
  const took = audit?.run_seconds
    ? ` Finished in ${formatDuration(audit.run_seconds)}.` : "";

  if (scoring.scoring_reliable === false) {
    return {
      key: UPLOAD_KEY,
      kind: "warning",
      title: "Scored — please check the warnings",
      message: `${who} scored ${score}, but some figures couldn't be ` +
        `verified. Review the warnings on their record before relying on ` +
        `this score.${took}`,
    };
  }

  const outcome = scoring.eligibility?.outcome;
  const eligibility = {
    met: "eligible",
    indeterminate: "eligibility needs your review",
    not_met: "does not meet the eligibility requirement",
  }[outcome];
  return {
    key: UPLOAD_KEY,
    kind: "success",
    title: "Scoring completed",
    message: `${who} scored ${score}` +
      `${eligibility ? ` — ${eligibility}` : ""}.${took}`,
  };
}

export function uploadFailed(error, fileName, elapsedMs = 0) {
  const file = fileName || "The CV";

  if (error?.kind === "unreachable") {
    if (elapsedMs < LOST_MIDWAY_MS) {
      return { ...serverUnreachable(`${file} wasn't uploaded.`), key: UPLOAD_KEY };
    }
    return {
      key: UPLOAD_KEY,
      kind: "error",
      title: "Lost contact with the server",
      message: `The server stopped responding while ${file} was being ` +
        `processed. Refresh the list in a few minutes — if the candidate ` +
        `isn't there, upload the CV again.`,
    };
  }

  const detail = error?.detail || error?.message || "";

  if (error?.status === 400) {
    const known = /unsupported file type/i.test(detail)
      || /filename/i.test(detail);
    return {
      key: UPLOAD_KEY,
      kind: "warning",
      title: "File not accepted",
      message: known
        ? `${file} can't be screened. Use a PDF or Word (.docx) file whose ` +
          `name has only letters, numbers, dots, dashes or underscores.`
        : `${file} wasn't accepted: ${cleanDetail(detail)}.`,
    };
  }

  if (error?.status === 422) {
    if (/no usable text layer|likely scanned/i.test(detail)) {
      return {
        key: UPLOAD_KEY,
        kind: "warning",
        title: "This CV can't be read",
        message: `${file} appears to be a scanned image with no selectable ` +
          `text, so it can't be scored. Upload a text-based PDF, or run it ` +
          `through OCR first.`,
      };
    }
    // A damaged PDF and a password-protected one produce the SAME message
    // from the extractor, so the wording names both rather than guessing.
    if (/text extraction failed/i.test(detail)) {
      return {
        key: UPLOAD_KEY,
        kind: "warning",
        title: "The file couldn't be opened",
        message: `${file} may be damaged or password-protected. Save an ` +
          `unprotected copy and upload it again.`,
      };
    }
    if (isModelFailure(detail)) {
      return {
        key: UPLOAD_KEY,
        kind: "error",
        title: "Processing failed",
        message: `The local AI model stopped responding while reading ` +
          `${file}, so it wasn't scored or added to the list. Check that ` +
          `Ollama is running, then upload the CV again.`,
      };
    }
    return {
      key: UPLOAD_KEY,
      kind: "error",
      title: "Processing failed",
      message: `${file} couldn't be scored: ${cleanDetail(detail)}. It ` +
        `wasn't added to the candidate list.`,
    };
  }

  if (error?.status >= 500) {
    return { ...serverFault(error, `processing ${file}`), key: UPLOAD_KEY };
  }

  return {
    key: UPLOAD_KEY,
    kind: "error",
    title: "Upload failed",
    message: `${file} couldn't be uploaded: ${cleanDetail(detail) || "unknown error"}.`,
  };
}

// --------------------------------------------------------------------------
// Overrides
// --------------------------------------------------------------------------

export function overrideReasonMissing() {
  return {
    key: OVERRIDE_KEY,
    kind: "warning",
    title: "A reason is required",
    message: "Add a short reason before recording the override. It's kept " +
      "in the audit log so the change can be understood later.",
  };
}

export function overrideRecorded(result, field) {
  const [beforeTotal, afterTotal] = result?.changed?.total || [];
  if (field === "eligibility") {
    const [before, after] = result?.changed?.eligibility || [];
    return {
      key: OVERRIDE_KEY,
      kind: "success",
      title: "Override recorded",
      message: `Eligibility changed from ${eligibilityLabel(before)} to ` +
        `${eligibilityLabel(after)}. The pipeline's own verdict is kept in ` +
        `the audit log.`,
    };
  }
  return {
    key: OVERRIDE_KEY,
    kind: "success",
    title: "Override recorded",
    message: `${criterionLabel(field)} updated. Effective total ` +
      `${twoDp(beforeTotal)} → ${twoDp(afterTotal)}; the computed score ` +
      `(${twoDp(result?.computed_total)}) is kept for the audit.`,
  };
}

/** "teaching_experience must be between 0 and its weight (20)" -> plain. */
function humaniseOverrideDetail(detail) {
  const range = /^(\w+) must be between 0 and its weight \(([\d.]+)\)/.exec(detail);
  if (range) {
    return `${criterionLabel(range[1])} must be between 0 and ${range[2]}.`;
  }
  return `${cleanDetail(detail)}.`;
}

export function overrideFailed(error) {
  if (error?.kind === "unreachable") {
    return serverUnreachable("The override wasn't recorded.");
  }
  const detail = error?.detail || error?.message || "";
  if (error?.status === 422 || error?.status === 400) {
    return {
      key: OVERRIDE_KEY,
      kind: "warning",
      title: "Override not accepted",
      message: humaniseOverrideDetail(detail),
    };
  }
  if (error?.status === 404) {
    return {
      key: OVERRIDE_KEY,
      kind: "warning",
      title: "Candidate not found",
      message: "This candidate no longer exists, so the override wasn't " +
        "recorded. Refresh the list.",
    };
  }
  if (error?.status >= 500) {
    return { ...serverFault(error, "recording the override"), key: OVERRIDE_KEY };
  }
  return {
    key: OVERRIDE_KEY,
    kind: "error",
    title: "Override not recorded",
    message: `${cleanDetail(detail) || "Unknown error"}.`,
  };
}

// --------------------------------------------------------------------------
// Written explanations
// --------------------------------------------------------------------------

export function explanationsReady(result) {
  const state = result?.state || {};
  const total = state.criteria_total;
  const written = state.llm_written;
  const rejected = result?.explanations_rejected || 0;

  // llm_used false means every criterion kept its template. Announcing
  // that as a success would tell the reviewer something changed when
  // nothing did.
  if (result && result.llm_used === false) {
    return {
      key: EXPLAIN_KEY,
      kind: "warning",
      title: "No written explanations were added",
      message: "The model's text couldn't be used, so the standard " +
        "explanations were kept. Scores are unchanged.",
    };
  }
  if (rejected > 0 && written != null && total != null) {
    return {
      key: EXPLAIN_KEY,
      kind: "success",
      title: "Explanations ready",
      message: `${written} of ${total} criteria now have written ` +
        `explanations. ${rejected} kept the standard wording because the ` +
        `model's text didn't match the computed figures. Scores are unchanged.`,
    };
  }
  return {
    key: EXPLAIN_KEY,
    kind: "success",
    title: "Explanations ready",
    message: "Written explanations were added to this candidate's " +
      "criteria. Scores are unchanged.",
  };
}

export function explanationsFailed(error) {
  if (error?.kind === "unreachable") {
    return serverUnreachable("The explanations weren't generated.");
  }
  const detail = error?.detail || error?.message || "";
  if (error?.status === 503 || isModelFailure(detail)) {
    return {
      key: EXPLAIN_KEY,
      kind: "error",
      title: "Couldn't generate explanations",
      message: "The local AI model didn't respond. Check that Ollama is " +
        "running, then try again. The standard explanations are still shown.",
    };
  }
  if (error?.status === 404) {
    return {
      key: EXPLAIN_KEY,
      kind: "warning",
      title: "Candidate not found",
      message: "This candidate no longer exists. Refresh the list.",
    };
  }
  return {
    key: EXPLAIN_KEY,
    kind: "error",
    title: "Couldn't generate explanations",
    message: `${cleanDetail(detail) || "Unknown error"}.`,
  };
}

// --------------------------------------------------------------------------
// Loading data, and the server coming back
// --------------------------------------------------------------------------

export function loadFailed(error, what) {
  if (error?.kind === "unreachable") {
    return serverUnreachable(`The scoring service isn't responding, so ${what} couldn't be loaded.`);
  }
  if (error?.status === 404) {
    return {
      key: LOAD_KEY,
      kind: "warning",
      title: "Candidate not found",
      message: "This candidate may have been removed. The list has been " +
        "refreshed.",
    };
  }
  if (error?.status >= 500) {
    return { ...serverFault(error, `loading ${what}`), key: LOAD_KEY };
  }
  return {
    key: LOAD_KEY,
    kind: "error",
    title: `Couldn't load ${what}`,
    message: `${cleanDetail(error?.detail || error?.message) || "Unknown error"}.`,
  };
}

export function serverRestored() {
  return {
    key: SERVER_KEY,
    kind: "success",
    title: "Connected again",
    message: "The server is responding again.",
  };
}
