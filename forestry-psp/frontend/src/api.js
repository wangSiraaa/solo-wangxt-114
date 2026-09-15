const BASE = import.meta.env.VITE_API_BASE || "/api";

async function request(method, path, body) {
  const options = { method, headers: {} };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const resp = await fetch(BASE + path, options);
  const text = await resp.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    throw new Error(`API ${path} 返回非 JSON（${resp.status}）`);
  }
  if (!resp.ok) {
    const detail = data?.detail;
    const message = Array.isArray(detail) ? detail.join("；") : detail || resp.statusText;
    const err = new Error(`${resp.status}: ${message}`);
    err.status = resp.status;
    throw err;
  }
  return data;
}

const get = (path) => request("GET", path);
const post = (path, body) => request("POST", path, body);
const patch = (path, body) => request("PATCH", path, body);

export const fetchPlots = () => get("/plots/");
export const fetchVersions = () => get("/survey-versions/");
export const fetchEstimates = (versionId) =>
  get(`/estimates/${versionId ? `?version_id=${versionId}` : ""}`);
export const fetchTickets = () => get("/verification-tickets/");
export const fetchRemeasurements = (plotId, versionId) =>
  get(
    `/plots/${plotId}/remeasurements/${versionId ? `?version_id=${versionId}` : ""}`
  );
export const fetchBatches = () => get("/revision-batches/");
export const createBatch = (payload) => post("/revision-batches/", payload);
export const applyBatch = (id) => post(`/revision-batches/${id}/apply/`);
export const retryBatch = (id) => post(`/revision-batches/${id}/retry/`);
export const patchConclusion = (id, afterValue) =>
  patch(`/revision-conclusions/${id}/`, { after_value: afterValue });
export const fetchCompare = (baseId, revisionId) =>
  get(`/survey-versions/compare/?base=${baseId}&revision=${revisionId}`);
