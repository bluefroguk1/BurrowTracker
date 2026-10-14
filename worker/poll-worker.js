/**
 * BurrowTracker poll relay - a Cloudflare Worker.
 *
 * Lets the "Check the store now" button on the GitHub Pages site start a poll without
 * the visitor needing a GitHub account. The GitHub token lives here as a secret,
 * never in the public page.
 *
 *   POST /poll    -> start a poll (refused if one ran or was requested in the last hour)
 *   GET  /status  -> { running, last_poll, next_allowed }
 *
 * Settings (Worker -> Settings -> Variables and Secrets):
 *   GITHUB_TOKEN  (secret)  fine-grained token, this repo only, "Actions: Read and write"
 *   REPO          (text)    e.g. bluefroguk1/BurrowTracker
 *   SITE_ORIGIN   (text)    e.g. https://bluefroguk1.github.io
 *   DATA_URL      (text)    e.g. https://bluefroguk1.github.io/BurrowTracker/data/models.json
 *   COOLDOWN_MINUTES (text, optional) default 60
 */

const WORKFLOW = "poll.yml";

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const cors = corsHeaders(request, env);

    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });
    if (cors === null) return json({ error: "This endpoint only accepts requests from the tracker page." }, 403, {});

    try {
      if (url.pathname === "/status" && request.method === "GET") {
        return json(await status(env), 200, cors);
      }
      if (url.pathname === "/poll" && request.method === "POST") {
        return await startPoll(env, cors);
      }
      return json({ error: "Not found" }, 404, cors);
    } catch (err) {
      return json({ error: "Something went wrong talking to GitHub. Try again later.", detail: String(err) }, 502, cors);
    }
  },
};

// ------------------------------------------------------------------ handlers
async function status(env) {
  const [lastPoll, runs] = await Promise.all([lastPollTime(env), recentRuns(env, 6)]);
  const running = runs.some((r) => r.status === "queued" || r.status === "in_progress" || r.status === "waiting");
  return { running, last_poll: lastPoll ? lastPoll.toISOString() : null, next_allowed: nextAllowed(env, lastPoll, runs) };
}

async function startPoll(env, cors) {
  const [lastPoll, runs] = await Promise.all([lastPollTime(env), recentRuns(env, cooldownMs(env) / 3600000 + 1)]);

  if (runs.some((r) => r.status === "queued" || r.status === "in_progress" || r.status === "waiting")) {
    return json({ started: false, running: true, message: "A check is already running." }, 202, cors);
  }
  const next = nextAllowed(env, lastPoll, runs);
  if (next && new Date(next) > new Date()) {
    return json({ started: false, running: false, next_allowed: next,
      message: "The store was checked recently. Manual checks are limited to one per hour." }, 429, cors);
  }

  const res = await gh(env, `/actions/workflows/${WORKFLOW}/dispatches`, {
    method: "POST",
    body: JSON.stringify({ ref: "main" }),
  });
  if (res.status !== 204) {
    const text = await res.text();
    return json({ error: "GitHub refused to start the check.", detail: `${res.status} ${text.slice(0, 300)}` }, 502, cors);
  }
  return json({ started: true, running: true, message: "Checking the store now." }, 202, cors);
}

// ------------------------------------------------------------------ helpers
function cooldownMs(env) {
  return (parseInt(env.COOLDOWN_MINUTES || "60", 10) || 60) * 60000;
}

/** Earliest time a manual poll may start: an hour after the last poll AND the last manual request. */
function nextAllowed(env, lastPoll, runs) {
  const times = [];
  if (lastPoll) times.push(lastPoll.getTime());
  for (const r of runs) if (r.event === "workflow_dispatch") times.push(new Date(r.created_at).getTime());
  if (!times.length) return null;
  return new Date(Math.max(...times) + cooldownMs(env)).toISOString();
}

async function lastPollTime(env) {
  try {
    const res = await fetch(`${env.DATA_URL}?t=${Date.now()}`, { cf: { cacheTtl: 0 }, headers: { "Cache-Control": "no-cache" } });
    if (!res.ok) return null;
    const data = await res.json();
    return data.last_poll ? new Date(data.last_poll) : null;
  } catch {
    return null;
  }
}

/** Runs of the poll workflow created in the last `hours` hours. */
async function recentRuns(env, hours) {
  const since = new Date(Date.now() - hours * 3600000).toISOString().slice(0, 19) + "Z";
  const res = await gh(env, `/actions/workflows/${WORKFLOW}/runs?per_page=20&created=%3E${encodeURIComponent(since)}`);
  if (!res.ok) throw new Error(`runs list: ${res.status}`);
  const data = await res.json();
  return (data.workflow_runs || []).filter((r) => r.event !== "push");
}

function gh(env, path, init = {}) {
  return fetch(`https://api.github.com/repos/${env.REPO}${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "BurrowTracker-poll-relay",
      ...(init.body ? { "Content-Type": "application/json" } : {}),
    },
  });
}

function corsHeaders(request, env) {
  const origin = request.headers.get("Origin");
  const allowed = (env.SITE_ORIGIN || "").split(",").map((s) => s.trim()).filter(Boolean);
  if (!origin || !allowed.includes(origin)) return null;
  return {
    "Access-Control-Allow-Origin": origin,
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Max-Age": "86400",
    Vary: "Origin",
  };
}

function json(body, status, headers) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store", ...headers },
  });
}
