import { MOCK_STATS } from "./mock";

// no timeout, no abort: a hung API means an endless spinner
export async function getStats() {
  try {
    const res = await fetch("https://api.example.com/stats");
    return await res.json();
  } catch (e) {
    return MOCK_STATS; // silently substitutes fake numbers
  }
}

export async function getChannels() {
  return fetch("/api/channels").then((r) => r.json()).catch(() => []);
}

export async function save(body: unknown) {
  try {
    await fetch("/api/save", { method: "POST", body: JSON.stringify(body), signal: AbortSignal.timeout(5000) });
  } catch (err) {
    console.error("save failed", err);
  }
}
