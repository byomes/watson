// web/pages/social.jsx
// Social seeds review: read from Vercel KV, edit, approve → write to queue

import { useState, useEffect } from "react";

export default function SocialReview() {
  const [seeds, setSeeds]     = useState([]);
  const [draft, setDraft]     = useState(null);
  const [status, setStatus]   = useState("loading");
  const [message, setMessage] = useState("");

  useEffect(() => {
    fetch("/api/get-draft")
      .then((r) => r.json())
      .then((data) => {
        if (data.error) {
          setMessage(data.error);
          setStatus("error");
          return;
        }
        setDraft(data);
        setSeeds(data.social_seeds || []);
        setStatus("idle");
      })
      .catch((e) => {
        setMessage("Failed to load draft: " + e.message);
        setStatus("error");
      });
  }, []);

  const updateSeed = (i, val) => {
    const updated = [...seeds];
    updated[i] = val;
    setSeeds(updated);
  };

  const handleApprove = async () => {
    setStatus("saving");
    setMessage("");
    try {
      const resp = await fetch("/api/approve-social", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          dated_slug: draft.dated_slug,
          seeds,
        }),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error || "Unknown error");
      setStatus("done");
      setMessage("✅ Social seeds approved and queued.");
    } catch (e) {
      setStatus("error");
      setMessage("❌ " + e.message);
    }
  };

  if (status === "loading") {
    return (
      <div style={styles.container}>
        <p style={styles.muted}>Loading seeds...</p>
      </div>
    );
  }

  return (
    <div style={styles.container}>
      <h1 style={styles.heading}>Social Seeds</h1>

      {draft && (
        <p style={styles.meta}>
          {draft.dated_slug} · {draft.generated_at}
        </p>
      )}

      <p style={styles.instructions}>
        Edit any seed before approving. These go to the social content queue.
      </p>

      {seeds.map((seed, i) => (
        <div key={i} style={styles.seedBlock}>
          <label style={styles.label}>Seed {i + 1}</label>
          <textarea
            style={styles.seedTextarea}
            value={seed}
            onChange={(e) => updateSeed(i, e.target.value)}
          />
          <p style={styles.charCount}>{seed.length} / 280</p>
        </div>
      ))}

      {message && (
        <p style={status === "error" ? styles.error : styles.success}>
          {message}
        </p>
      )}

      <div style={styles.actions}>
        <button
          style={status === "done" ? styles.btnDisabled : styles.btnPrimary}
          onClick={handleApprove}
          disabled={status === "saving" || status === "done"}
        >
          {status === "saving" ? "Saving..." : "Approve Seeds"}
        </button>
        <a href="/" style={styles.link}>
          ← Back to blog review
        </a>
      </div>
    </div>
  );
}

const styles = {
  container: {
    maxWidth: 680,
    margin: "0 auto",
    padding: "24px 16px",
    fontFamily: "system-ui, sans-serif",
    color: "#1a1a1a",
  },
  heading:      { fontSize: 24, fontWeight: 700, marginBottom: 4 },
  meta:         { color: "#666", fontSize: 13, marginBottom: 8 },
  instructions: { color: "#555", fontSize: 14, marginBottom: 20 },
  label:        { display: "block", fontWeight: 600, marginBottom: 4 },
  seedBlock:    { marginBottom: 20 },
  seedTextarea: {
    width: "100%",
    height: 80,
    padding: "10px 12px",
    fontSize: 14,
    lineHeight: 1.5,
    border: "1px solid #ddd",
    borderRadius: 6,
    boxSizing: "border-box",
    resize: "vertical",
  },
  charCount: { fontSize: 12, color: "#999", marginTop: 4, textAlign: "right" },
  actions: { display: "flex", alignItems: "center", gap: 20, marginTop: 24 },
  btnPrimary: {
    padding: "12px 28px",
    background: "#1a1a1a",
    color: "#fff",
    border: "none",
    borderRadius: 6,
    fontSize: 16,
    fontWeight: 600,
    cursor: "pointer",
  },
  btnDisabled: {
    padding: "12px 28px",
    background: "#aaa",
    color: "#fff",
    border: "none",
    borderRadius: 6,
    fontSize: 16,
    fontWeight: 600,
    cursor: "not-allowed",
  },
  link:    { color: "#555", fontSize: 14 },
  muted:   { color: "#888" },
  error:   { color: "#c00", marginTop: 12 },
  success: { color: "#080", marginTop: 12 },
};
