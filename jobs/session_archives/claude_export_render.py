"""jobs/session_archives/claude_export_render.py — turn one conversation object
from a Claude.ai account-data export (conversations-*.zip -> conversations.json)
into the (transcript, files, title, summary) shape archive_session() expects.

Shared by jobs/session_archives/claude_export_import.py (nightly) and
jobs/session_archives/backfill_reclassify.py (one-time backlog sort) so the
two don't drift into rendering the same export format two different ways.
"""
import base64
import json

_LANG_EXT = {
    "python": ".py", "javascript": ".js", "typescript": ".ts", "html": ".html",
    "css": ".css", "json": ".json", "bash": ".sh", "shell": ".sh", "markdown": ".md",
    "jsx": ".jsx", "tsx": ".tsx", "sql": ".sql", "yaml": ".yaml", "xml": ".xml",
}
_MIME_EXT = {
    "text/markdown": ".md", "text/html": ".html", "text/plain": ".txt",
    "application/json": ".json",
}


def _guess_ext(mime, language):
    if language and language.lower() in _LANG_EXT:
        return _LANG_EXT[language.lower()]
    return _MIME_EXT.get(mime, ".txt")


def _render_block(block):
    t = block.get("type")
    if t == "text":
        return block.get("text", "")
    if t == "thinking":
        return f"[thinking] {block.get('thinking', '')}"
    if t == "tool_use":
        name = block.get("name", "unknown_tool")
        inp = block.get("input") or {}
        if name == "artifacts" and inp.get("content"):
            return f'[tool_use: artifacts {inp.get("command")} "{inp.get("title") or inp.get("id")}"] (content captured as an attached file)'
        if name in ("create_file", "file_create") and inp.get("file_text"):
            return f'[tool_use: {name} "{inp.get("path") or inp.get("filename")}"] (content captured as an attached file)'
        return f"[tool_use: {name}] input={json.dumps(inp)[:2000]}"
    if t == "tool_result":
        content = block.get("content")
        if isinstance(content, list):
            rendered = " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
        elif isinstance(content, str):
            rendered = content
        else:
            rendered = ""
        return f"[tool_result: {block.get('name', '?')}] {rendered[:2000]}"
    if t == "voice_note":
        return f"[voice_note: {block.get('title', '')}] {block.get('text', '')}"
    if t == "token_budget":
        return None
    return f"[{t}] {json.dumps(block)[:500]}"


def _render_message(m):
    sender = "Human" if m.get("sender") == "human" else "Assistant"
    parts = []
    for block in (m.get("content") or []):
        r = _render_block(block)
        if r:
            parts.append(r)
    for att in (m.get("attachments") or []):
        fn = att.get("file_name", "attachment")
        extracted = att.get("extracted_content", "")
        if extracted:
            parts.append(f"[Attached file: {fn}]\n{extracted}")
        else:
            parts.append(f"[Attached file: {fn}] (no extracted text content in export)")
    for f in (m.get("files") or []):
        parts.append(f"[File reference: {f.get('file_name', '?')}] (binary content not included in this export format)")
    body = "\n".join(parts).strip()
    return f"{sender}: {body}" if body else f"{sender}: (empty message)"


def render_transcript(conversation: dict) -> str:
    rendered = "\n\n".join(_render_message(m) for m in conversation.get("chat_messages") or [])
    return rendered or "(empty conversation - no messages)"


def extract_files(conversation: dict) -> list:
    files = []
    for m in conversation.get("chat_messages") or []:
        for block in (m.get("content") or []):
            if block.get("type") != "tool_use":
                continue
            name = block.get("name")
            inp = block.get("input") or {}
            if name == "artifacts" and inp.get("command") in (None, "create", "rewrite") and inp.get("content"):
                title = inp.get("title") or inp.get("id") or "artifact"
                ext = _guess_ext(inp.get("type"), inp.get("language"))
                filename = title if "." in title else f"{title}{ext}"
                files.append({"filename": filename, "content_base64": base64.b64encode(inp["content"].encode("utf-8")).decode()})
            elif name in ("create_file", "file_create") and inp.get("file_text"):
                path = inp.get("path") or inp.get("filename") or "file.txt"
                filename = path.rsplit("/", 1)[-1]
                files.append({"filename": filename, "content_base64": base64.b64encode(inp["file_text"].encode("utf-8")).decode()})
    return files


def build_title(conversation: dict) -> str:
    name = (conversation.get("name") or "").strip()
    if name:
        return name[:200]
    chat_messages = conversation.get("chat_messages") or []
    first_human = next((m for m in chat_messages if m.get("sender") == "human"), None)
    snippet = (first_human.get("text", "") if first_human else "")[:60].strip()
    date = (conversation.get("created_at") or "")[:10]
    return (f"(untitled) {date} - {snippet}" if snippet else f"(untitled) {date}")[:200]


def build_summary(conversation: dict) -> str:
    sm = (conversation.get("summary") or "").strip()
    if sm:
        return sm[:4000]
    return "No auto-generated summary available for this conversation; see full transcript for content."
