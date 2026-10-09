const GH_OWNER = "ali12hchcfxkzy";
const GH_REPO = "ALI-VIP-BOT-ALI";
const GH_BRANCH = "main";

export default {
  async fetch(request, env) {
    const headers = {
      "Content-Type": "application/json",
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type"
    };

    if (request.method === "OPTIONS") {
      return new Response(null, { headers });
    }

    const url = new URL(request.url);

    if (url.pathname !== "/verify" || request.method !== "POST") {
      return Response.json(
        { error: "Not found" },
        { status: 404, headers }
      );
    }

    if (!env.GH_TOKEN) {
      return Response.json(
        { valid: false, error: "server_configuration_error" },
        { status: 500, headers }
      );
    }

    try {
      const body = await request.json();
      const key = String(body.key || "").trim();

      if (!key || key.length > 100) {
        return Response.json(
          { valid: false, error: "invalid_key" },
          { status: 400, headers }
        );
      }

      const api =
        `https://api.github.com/repos/${GH_OWNER}/${GH_REPO}` +
        `/contents/keylist.json?ref=${GH_BRANCH}`;

      const response = await fetch(api, {
        headers: {
          Authorization: `Bearer ${env.GH_TOKEN}`,
          Accept: "application/vnd.github.raw+json",
          "X-GitHub-Api-Version": "2022-11-28",
          "User-Agent": "ALI-VIP-Worker"
        }
      });

      if (!response.ok) {
        return Response.json(
          { valid: false, error: "key_service_unavailable" },
          { status: 503, headers }
        );
      }

      const keys = await response.json();
      const record = keys[key];

      if (!record || record.active !== true) {
        return Response.json(
          { valid: false, error: "invalid_or_banned_key" },
          { headers }
        );
      }

      const expiry = String(record.expiry || "");

      if (!/^\d{4}-\d{2}-\d{2}$/.test(expiry)) {
        return Response.json(
          { valid: false, error: "invalid_expiry" },
          { headers }
        );
      }

      const expiryTime = Date.parse(`${expiry}T23:59:59.999Z`);

      if (!Number.isFinite(expiryTime) || Date.now() > expiryTime) {
        return Response.json(
          { valid: false, error: "expired" },
          { headers }
        );
      }

      return Response.json(
        { valid: true, expiry },
        { headers }
      );
    } catch {
      return Response.json(
        { valid: false, error: "server_error" },
        { status: 500, headers }
      );
    }
  }
};
