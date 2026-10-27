/** Thin wrappers over the Stage 8 API. No logic beyond HTTP. */

const BASE = "/api";

async function request(path, options) {
  const response = await fetch(BASE + path, options);
  const text = await response.text();
  let body;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = { detail: text };
  }
  if (!response.ok) {
    const detail =
      typeof body?.detail === "string"
        ? body.detail
        : JSON.stringify(body?.detail ?? body);
    throw new Error(detail || `HTTP ${response.status}`);
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
