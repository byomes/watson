// web/pages/index.jsx
// Blog post review: read from Vercel KV, edit, approve → push to wcky

import { useState, useEffect } from "react";

export default function BlogReview() {
  const [draft, setDraft]       = useState(null);
  const [body, setBody]         = useState("");
  const [title, setTitle]       = useState("");
  const [status, setStatus]     = useState("idle"); // idle | loading | saving | done | error
  const [message, setMessage]   = useState("");

  useEffect(() => {
    setStatus("loading");
    fetch("/api/get-draft")
      .then((r) => r.json())
      .then((data) => {
        if (data.error) {
          setMessage(data.error);
          setStatus("error");
          return;
        }
        setDraft(data);
        setTitle(data.title || "");
        // Strip frontmatter from body for editing
        const bodyOnly = data.blog_md
          ? data.blog_md.replace(/^---[\s\S]*?---\n\n?/, "")
          : "";
        setBody(bodyOnly);
        setStatus("idle");
      })
      .catch((e) => {
        setMessage("Failed to load draft: " + e.message);
        setStatus("error");
      });
  }, []);

  const handleApprove = async () => {
    setStatus("saving");
    setMessage("");
    try {
      const resp = await fetch("/api/approve-blog", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          dated_slug:  draft.dated_slug,
          title:       title,
          description: draft.description,
          body:        body,
        }),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error || "Unknown error");
      setStatus("done");
      setMessage("✅ Post approved and pushed to blog.");
    } catch (e) {
      setStatus("error");
      setMessage("❌ " + e.message);
    }
  };

  if (status === "loading") {
    return (
      <div style={styles.container}>
        <p style={styles.muted}>Loading draft...</p>
      </div>
    );
  }

  if (!draft && status !== "loading") {
    return (
      <div style={styles.container}>
        <h1 style={styles.heading}>Watson Review</h1>
        <p style={styles.muted}>{message || "No draft available."}</p>
      </div>
    );
  }

  return (
    <div style={styles.container}>
      <h1 style={styles.heading}>Blog Review</h1>

      {draft && (
        <p style={styles.meta}>
          {draft.dated_slug} · {draft.generated_at}
        </p>
      )}

      <label style={styles.label}>Title</label>
      <input
        style={styles.input}
        value={title}
        onChange={(e) => setTitle(e.target.value)}
      />

      <label style={styles.label}>Body</label>
      <textarea
        style={styles.textarea}
        value={body}
        onChange={(e) => setBody(e.target.value)}
      />

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
          {status === "saving" ? "Publishing..." : "Approve & Publish"}
        </button>
        <a href="/social" style={styles.link}>
          Review social seeds →
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
  heading: { fontSize: 24, fontWeight: 700, marginBottom: 4 },
  meta:    { color: "#666", fontSize: 13, marginBottom: 20 },
  label:   { display: "block", fontWeight: 600, marginBottom: 4, marginTop: 16 },
  input: {
    width: "100%",
    padding: "10px 12px",
    fontSize: 16,
    border: "1px solid #ddd",
    borderRadius: 6,
    boxSizing: "border-box",
  },
  textarea: {
    width: "100%",
    height: 480,
    padding: "10px 12px",
    fontSize: 14,
    lineHeight: 1.6,
    border: "1px solid #ddd",
    borderRadius: 6,
    boxSizing: "border-box",
    fontFamily: "monospace",
    resize: "vertical",
  },
  actions: { display: "flex", alignItems: "center", gap: 20, marginTop: 20 },
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
