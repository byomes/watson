// web/pages/api/approve-social.js
// Receives approved social seeds, writes them to the KV social queue,
// and marks the draft as social-approved.

export default async function handler(req, res) {
  if (req.method !== "POST") {
    return res.status(405).json({ error: "Method not allowed" });
  }

  const { dated_slug, seeds } = req.body;

  if (!dated_slug || !Array.isArray(seeds)) {
    return res.status(400).json({ error: "Missing dated_slug or seeds" });
  }

  const kvUrl   = process.env.VERCEL_KV_REST_API_URL;
  const kvToken = process.env.VERCEL_KV_REST_API_TOKEN;

  if (!kvUrl || !kvToken) {
    return res.status(500).json({ error: "Vercel KV not configured" });
  }

  const headers = {
    Authorization: `Bearer ${kvToken}`,
    "Content-Type": "application/json",
  };

  try {
    // Write seeds to the social queue key
    const queueEntry = {
      dated_slug,
      seeds,
      approved_at: new Date().toISOString(),
      status: "queued",
    };

    const setResp = await fetch(`${kvUrl}/set/social:queue:${dated_slug}`, {
      method: "POST",
      headers,
      body: JSON.stringify(queueEntry),
    });

    if (!setResp.ok) {
      return res.status(502).json({ error: "KV write failed" });
    }

    // Also append key to the social queue index so the social job can list all pending
    let queueIndex = [];
    try {
      const idxResp = await fetch(`${kvUrl}/get/social:queue:index`, {
        headers: { Authorization: `Bearer ${kvToken}` },
      });
      if (idxResp.ok) {
        const idxData = await idxResp.json();
        queueIndex = idxData.result
          ? (typeof idxData.result === "string"
              ? JSON.parse(idxData.result)
              : idxData.result)
          : [];
      }
    } catch (_) {}

    if (!queueIndex.includes(dated_slug)) {
      queueIndex.push(dated_slug);
      await fetch(`${kvUrl}/set/social:queue:index`, {
        method: "POST",
        headers,
        body: JSON.stringify(queueIndex),
      });
    }

    // Update the sermon:current draft status
    try {
      const kvGet = await fetch(`${kvUrl}/get/sermon:current`, {
        headers: { Authorization: `Bearer ${kvToken}` },
      });
      if (kvGet.ok) {
        const kvData = await kvGet.json();
        const draft  = typeof kvData.result === "string"
          ? JSON.parse(kvData.result)
          : kvData.result;
        draft.social_approved = true;
        draft.status = draft.blog_approved ? "approved" : "social_approved";
        await fetch(`${kvUrl}/set/sermon:current`, {
          method: "POST",
          headers,
          body: JSON.stringify(draft),
        });
      }
    } catch (_) {}

    return res.status(200).json({ ok: true, queued: dated_slug });
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
}
