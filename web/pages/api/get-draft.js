// web/pages/api/get-draft.js
// Reads the current sermon draft from Vercel KV

export default async function handler(req, res) {
  if (req.method !== "GET") {
    return res.status(405).json({ error: "Method not allowed" });
  }

  const kvUrl   = process.env.VERCEL_KV_REST_API_URL;
  const kvToken = process.env.VERCEL_KV_REST_API_TOKEN;

  if (!kvUrl || !kvToken) {
    return res.status(500).json({ error: "Vercel KV not configured" });
  }

  try {
    const resp = await fetch(`${kvUrl}/get/sermon:current`, {
      headers: { Authorization: `Bearer ${kvToken}` },
    });

    if (!resp.ok) {
      return res.status(502).json({ error: "KV fetch failed" });
    }

    const data = await resp.json();

    if (!data.result) {
      return res.status(404).json({ error: "No draft available" });
    }

    // Vercel KV returns the value as a JSON string inside data.result
    const draft =
      typeof data.result === "string" ? JSON.parse(data.result) : data.result;

    return res.status(200).json(draft);
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
}
