/** Thin wrappers over the Stage 8 API. No logic beyond HTTP. */

const BASE = "/api";

/**
 * A failed request, with enough shape for the UI to say WHAT went wrong.
 *
 *   kind "unreachable"  the server never answered. fetch() rejected, or
 *                       the dev proxy answered on the backend's behalf -
 *                       Vite replies 500 with an EMPTY body when it cannot
 *                       reach FastAPI, while a real FastAPI 500 always
 *                       carries "Internal Server Error". A 502/503/504
 *                       WITHOUT a JSON body is a gateway failure and means
 *                       the same thing. With one it is not: the explain
 *                       endpoint itself answers 503 + detail when the model
 *                       fails, and that server is plainly reachable.
 *   kind "http"         the server answered with an error. `status` and
 *                       `detail` say which.
 *
 * `message` stays the plain detail string, so existing callers that only
 * read exc.message keep working.
 */
export class ApiError extends Error {
  constructor(message, { kind, status = null, detail = "" } = {}) {
    super(message);
    this.name = "ApiError";
    this.kind = kind;
    this.status = status;
    this.detail = detail;
  }
}

const GATEWAY_FAILURES = new Set([502, 503, 504]);

/** Read FastAPI's `detail`, which is a string or a list of field errors. */
export function readDetail(body) {
  const detail = body?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => (typeof item === "string" ? item : item?.msg))
      .filter(Boolean)
      .join("; ");
  }
  return detail == null ? "" : JSON.stringify(detail);
}

async function request(path, options) {
  let response;
  try {
    response = await fetch(BASE + path, options);
  } catch (exc) {
    throw new ApiError("The server could not be reached.", {
      kind: "unreachable", detail: String(exc?.message || exc),
    });
  }
  const text = await response.text();
  let body;
  let isJson = false;
  try {
    body = text ? JSON.parse(text) : null;
    isJson = body !== null;
  } catch {
    body = { detail: text };
  }
  if (!response.ok) {
    const unreachable =
      !isJson &&
      (GATEWAY_FAILURES.has(response.status) ||
        (response.status === 500 && !text.trim()));
    const detail = readDetail(body);
    throw new ApiError(detail || `HTTP ${response.status}`, {
      kind: unreachable ? "unreachable" : "http",
      status: response.status,
      detail,
    });
  }
  return body;
}

export const listCandidates = () => request("/candidates");

export const getCandidate = (id) =>
  request(`/candidates/${encodeURIComponent(id)}`);

/**
 * Prose is off by default, matching the API. It cannot change a score, and
 * generating it during upload costs roughly a quarter of the run for text
 * the reviewer may never open. Ask for it per candidate instead.
 */
export function uploadCV(file, { useLlm = false } = {}) {
  const form = new FormData();
  form.append("file", file);
  return request(`/candidates?use_llm=${useLlm}`, {
    method: "POST",
    body: form,
  });
}

export const generateExplanations = (id) =>
  request(`/candidates/${encodeURIComponent(id)}/explain`, { method: "POST" });

export function overrideScore(id, payload) {
  return request(`/candidates/${encodeURIComponent(id)}/score`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}
