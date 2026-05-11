// web/pages/api/approve-blog.js
// Receives approved blog post, pushes .md to byomes/wcky via GitHub API,
// then marks the KV draft as approved.

export default async function handler(req, res) {
  if (req.method !== "POST") {
    return res.status(405).json({ error: "Method not allowed" });
  }

  const { dated_slug, title, description, body } = req.body;

  if (!dated_slug || !body) {
    return res.status(400).json({ error: "Missing dated_slug or body" });
  }

  const githubToken = process.env.WCKY_GITHUB_TOKEN;
  const wckyRepo    = process.env.WCKY_GITHUB_REPO || "byomes/wcky";

  if (!githubToken) {
    return res.status(500).json({ error: "WCKY_GITHUB_TOKEN not configured" });
  }

  // Rebuild frontmatter from whatever the user kept/edited
  const today = dated_slug.split("-").slice(0, 3).join("-");
  const slug  = dated_slug.replace(/^\d{4}-\d{2}-\d{2}-/, "");

  const mdContent = [
    "---",
    `title: "${title}"`,
    `date: "${today}"`,
    `description: "${description}"`,
    `slug: "${slug}"`,
    "---",
    "",
    body.trim(),
    "",
  ].join("\n");

  const filePath    = `content/blog/${dated_slug}.md`;
  const encodedBody = Buffer.from(mdContent).toString("base64");

  try {
    // Check if file already exists (needed to get its SHA for updates)
    const checkResp = await fetch(
      `https://api.github.com/repos/${wckyRepo}/contents/${filePath}`,
      {
        headers: {
          Authorization: `token ${githubToken}`,
          Accept: "application/vnd.github+json",
        },
      }
    );

    let sha = undefined;
    if (checkResp.ok) {
      const existing = await checkResp.json();
      sha = existing.sha;
    }

    // Create or update the file
    const pushBody = {
      message: `feat: add sermon blog post ${dated_slug}`,
      content: encodedBody,
      ...(sha ? { sha } : {}),
    };

    const pushResp = await fetch(
      `https://api.github.com/repos/${wckyRepo}/contents/${filePath}`,
      {
        method: "PUT",
        headers: {
          Authorization: `token ${githubToken}`,
          Accept: "application/vnd.github+json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify(pushBody),
      }
    );

    if (!pushResp.ok) {
      const err = await pushResp.json();
      return res.status(502).json({ error: err.message || "GitHub push failed" });
    }

    // Mark KV draft as approved
    const kvUrl   = process.env.VERCEL_KV_REST_API_URL;
    const kvToken = process.env.VERCEL_KV_REST_API_TOKEN;
    if (kvUrl && kvToken) {
      // Read current, update status, write back
      try {
        const kvGet = await fetch(`${kvUrl}/get/sermon:current`, {
          headers: { Authorization: `Bearer ${kvToken}` },
        });
        if (kvGet.ok) {
          const kvData  = await kvGet.json();
          const draft   = typeof kvData.result === "string"
            ? JSON.parse(kvData.result)
            : kvData.result;
          draft.blog_approved = true;
          draft.status = draft.social_approved ? "approved" : "blog_approved";
          await fetch(`${kvUrl}/set/sermon:current`, {
            method: "POST",
            headers: {
              Authorization: `Bearer ${kvToken}`,
              "Content-Type": "application/json",
            },
            body: JSON.stringify(draft),
          });
        }
      } catch (_) {
        // KV status update is non-critical — don't fail the response
      }
    }

    return res.status(200).json({ ok: true, file: filePath });
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
}
