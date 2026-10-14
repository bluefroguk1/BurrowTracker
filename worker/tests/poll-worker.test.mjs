import worker from "../poll-worker.js";
import assert from "node:assert/strict";
const env = { GITHUB_TOKEN: "t", REPO: "o/r", SITE_ORIGIN: "https://o.github.io", DATA_URL: "https://o.github.io/r/data/models.json" };
let state;
globalThis.fetch = async (url, init = {}) => {
  url = String(url);
  if (url.startsWith(env.DATA_URL)) return new Response(JSON.stringify({ last_poll: state.lastPoll }));
  if (url.includes("/runs?")) { state.runsQueried = url; return new Response(JSON.stringify({ workflow_runs: state.runs })); }
  if (url.endsWith("/dispatches")) { assert.equal(init.method, "POST"); assert.match(init.headers.Authorization, /Bearer t/); state.dispatched++; return new Response(null, { status: 204 }); }
  throw new Error("unexpected " + url);
};
const req = (path, method = "POST", origin = "https://o.github.io") => new Request("https://w.dev" + path, { method, headers: origin ? { Origin: origin } : {} });
const ago = (min) => new Date(Date.now() - min * 60000).toISOString();
async function call(r) { const res = await worker.fetch(r, env); return [res.status, await res.json().catch(() => null), res]; }

// 1. old poll, no runs -> dispatch
state = { lastPoll: ago(120), runs: [], dispatched: 0 };
let [s, b, res] = await call(req("/poll"));
assert.equal(s, 202); assert.equal(b.started, true); assert.equal(state.dispatched, 1);
assert.equal(res.headers.get("Access-Control-Allow-Origin"), "https://o.github.io");
// 2. polled 20 min ago -> 429
state = { lastPoll: ago(20), runs: [], dispatched: 0 };
[s, b] = await call(req("/poll")); assert.equal(s, 429); assert.equal(state.dispatched, 0); assert.ok(b.next_allowed);
// 3. run in progress -> 202 running, no dispatch
state = { lastPoll: ago(120), runs: [{ status: "in_progress", event: "workflow_dispatch", created_at: ago(1) }], dispatched: 0 };
[s, b] = await call(req("/poll")); assert.equal(s, 202); assert.equal(b.started, false); assert.equal(b.running, true); assert.equal(state.dispatched, 0);
// 4. manual request 30 min ago finished (rate-limited run) -> 429 even though last_poll old
state = { lastPoll: ago(120), runs: [{ status: "completed", event: "workflow_dispatch", created_at: ago(30) }], dispatched: 0 };
[s] = await call(req("/poll")); assert.equal(s, 429);
// 5. push runs ignored
state = { lastPoll: ago(120), runs: [{ status: "in_progress", event: "push", created_at: ago(1) }], dispatched: 0 };
[s, b] = await call(req("/poll")); assert.equal(b.started, true);
// 6. wrong origin / no origin -> 403
[s] = await call(req("/poll", "POST", "https://evil.example")); assert.equal(s, 403);
[s] = await call(req("/poll", "POST", null)); assert.equal(s, 403);
// 7. status
state = { lastPoll: ago(10), runs: [{ status: "queued", event: "schedule", created_at: ago(1) }], dispatched: 0 };
[s, b] = await call(req("/status", "GET")); assert.equal(s, 200); assert.equal(b.running, true); assert.ok(b.next_allowed);
// 8. preflight
[s, , res] = [ (await worker.fetch(req("/poll", "OPTIONS"), env)).status ]; assert.equal(s, 204);
console.log("worker tests OK");
