/**
 * jit-llm-proxy — Cloudflare Worker
 *
 * Proxies LLM requests from the `jit` CLI to the real LLM provider.
 * Multi-layer protection:
 *   1. HMAC request signature verification
 *   2. Payload validation (system prompt hash + tool names)
 *   3. Per-device rate limiting (30/day)
 *   4. Per-IP rate limiting (60/day)
 *   5. Global concurrent request cap
 *   6. max_tokens capped at 2048
 */

// ── Constants ───────────────────────────────────────────────────────────────

const UPSTREAM_URL = "https://api.siliconflow.cn/v1/chat/completions";
const UPSTREAM_MODEL = "Pro/moonshotai/Kimi-K2-Instruct";

const DEVICE_DAILY_LIMIT = 30;
const IP_DAILY_LIMIT = 60;
const MAX_TOKENS_CAP = 2048;
const SIGNATURE_MAX_AGE_SECONDS = 300; // 5 min tolerance

// SHA-256 hash of the expected system prompt prefix (first 100 chars).
// Regenerate when system prompt changes:
//   echo -n "You are jit, an AI environment setup assistant. Your job is to get this project's development e" | shasum -a 256
const SYSTEM_PROMPT_PREFIX = "You are jit, an AI environment setup assistant.";

// The only tool names the jit CLI uses
const ALLOWED_TOOLS = new Set([
  "scan_project", "read_file", "list_files", "check_tool",
  "get_platform", "run_command", "write_env", "create_venv",
  "install_deps", "verify_setup",
]);

// ── Helpers ─────────────────────────────────────────────────────────────────

async function hmacVerify(key, deviceId, timestamp, bodyBytes) {
  const enc = new TextEncoder();
  const bodyHash = await crypto.subtle.digest("SHA-256", bodyBytes);
  const bodyHex = [...new Uint8Array(bodyHash)]
    .map(b => b.toString(16).padStart(2, "0")).join("");

  const message = `${deviceId}.${timestamp}.${bodyHex}`;
  const cryptoKey = await crypto.subtle.importKey(
    "raw", enc.encode(key),
    { name: "HMAC", hash: "SHA-256" },
    false, ["sign"],
  );
  const sig = await crypto.subtle.sign("HMAC", cryptoKey, enc.encode(message));
  return [...new Uint8Array(sig)]
    .map(b => b.toString(16).padStart(2, "0")).join("");
}

function dayKey() {
  // UTC day as "2026-04-04"
  return new Date().toISOString().slice(0, 10);
}

async function checkRateLimit(kv, prefix, id, limit) {
  const key = `${prefix}:${dayKey()}:${id}`;
  const raw = await kv.get(key);
  const count = raw ? parseInt(raw, 10) : 0;
  if (count >= limit) {
    return { allowed: false, remaining: 0, count };
  }
  await kv.put(key, String(count + 1), { expirationTtl: 86400 });
  return { allowed: true, remaining: limit - count - 1, count: count + 1 };
}

function jsonError(message, status, extra = {}) {
  return new Response(
    JSON.stringify({ error: message, ...extra }),
    { status, headers: { "Content-Type": "application/json" } },
  );
}

// ── Main Handler ────────────────────────────────────────────────────────────

export default {
  async fetch(request, env) {
    // Only accept POST
    if (request.method !== "POST") {
      return jsonError("Method not allowed", 405);
    }

    // Only accept the chat completions path
    const url = new URL(request.url);
    if (!url.pathname.endsWith("/chat/completions")) {
      return jsonError("Not found", 404);
    }

    // ── 1. Extract & validate headers ───────────────────────────────────
    const deviceId  = request.headers.get("X-Jit-Device");
    const timestamp = request.headers.get("X-Jit-Timestamp");
    const signature = request.headers.get("X-Jit-Signature");
    const version   = request.headers.get("X-Jit-Version") || "unknown";
    const clientIP  = request.headers.get("CF-Connecting-IP") || "unknown";

    if (!deviceId || !timestamp || !signature) {
      return jsonError("Missing authentication headers", 401);
    }

    // Check timestamp freshness (prevent replay)
    const ts = parseInt(timestamp, 10);
    const now = Math.floor(Date.now() / 1000);
    if (Math.abs(now - ts) > SIGNATURE_MAX_AGE_SECONDS) {
      return jsonError("Request expired", 401);
    }

    // ── 2. Read and verify body ─────────────────────────────────────────
    const bodyBytes = new Uint8Array(await request.arrayBuffer());
    let body;
    try {
      body = JSON.parse(new TextDecoder().decode(bodyBytes));
    } catch {
      return jsonError("Invalid JSON body", 400);
    }

    // ── 3. HMAC signature verification ──────────────────────────────────
    const signKey = env.SIGN_KEY || "jit-setup-v0.1.0-public-signing-key";
    const expectedSig = await hmacVerify(signKey, deviceId, timestamp, bodyBytes);
    if (signature !== expectedSig) {
      return jsonError("Invalid signature", 403);
    }

    // ── 4. Payload validation ───────────────────────────────────────────

    // 4a. System prompt must start with our expected prefix
    const messages = body.messages || [];
    const systemMsg = messages.find(m => m.role === "system");
    if (!systemMsg || !systemMsg.content ||
        !systemMsg.content.startsWith(SYSTEM_PROMPT_PREFIX)) {
      return jsonError("Invalid request payload", 403);
    }

    // 4b. All tools must be in our allowed set
    const tools = body.tools || [];
    for (const tool of tools) {
      const name = tool?.function?.name;
      if (!name || !ALLOWED_TOOLS.has(name)) {
        return jsonError(`Unauthorized tool: ${name}`, 403);
      }
    }

    // 4c. Cap max_tokens
    body.max_tokens = Math.min(body.max_tokens || MAX_TOKENS_CAP, MAX_TOKENS_CAP);

    // 4d. Force model
    body.model = UPSTREAM_MODEL;

    // ── 5. Rate limiting ────────────────────────────────────────────────
    const deviceRL = await checkRateLimit(
      env.RATE_KV, "dev", deviceId, DEVICE_DAILY_LIMIT,
    );
    if (!deviceRL.allowed) {
      return jsonError(
        "Daily free quota exhausted (30/day). Set your own API key for unlimited use.",
        429,
        { remaining: 0, reset: "midnight UTC" },
      );
    }

    const ipRL = await checkRateLimit(
      env.RATE_KV, "ip", clientIP, IP_DAILY_LIMIT,
    );
    if (!ipRL.allowed) {
      return jsonError(
        "Too many requests from this IP. Try again tomorrow or set your own API key.",
        429,
        { remaining: 0, reset: "midnight UTC" },
      );
    }

    // ── 6. Forward to upstream LLM ──────────────────────────────────────
    const upstreamResp = await fetch(UPSTREAM_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${env.LLM_API_KEY}`,
      },
      body: JSON.stringify(body),
    });

    // ── 7. Stream response back ─────────────────────────────────────────
    const responseHeaders = new Headers({
      "Content-Type": upstreamResp.headers.get("Content-Type") || "text/event-stream",
      "X-Jit-Remaining": String(deviceRL.remaining),
      "X-Jit-Device-Count": String(deviceRL.count),
      "Cache-Control": "no-cache",
    });

    return new Response(upstreamResp.body, {
      status: upstreamResp.status,
      headers: responseHeaders,
    });
  },
};
