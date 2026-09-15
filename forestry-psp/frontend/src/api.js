const BASE = import.meta.env.VITE_API_BASE || "/api";

async function get(path) {
  const resp = await fetch(BASE + path);
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`API ${path} 返回 ${resp.status}: ${text.slice(0, 200)}`);
  }
  return resp.json();
}

export const fetchPlots = () => get("/plots/");
export const fetchEstimates = () => get("/estimates/");
export const fetchTickets = () => get("/verification-tickets/");
export const fetchRemeasurements = (plotId) =>
  get(`/plots/${plotId}/remeasurements/`);
