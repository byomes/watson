"""jobs/privacy/verify.py — check_success(): verifies a genuine post-action
completion signal on a Playwright page. Shared by remove.py's _submit_wizard
(post-submit-click check) and confirm.py (post-confirmation-link-click
check) — same success_check shape, same "no config -> explicit failure,
never a silent pass" rule in both places.
"""


async def check_success(page, success_check: dict | None) -> tuple[bool, str | None]:
    """success_check is captured live per broker (never guessed) as one of:
      {"type": "url_contains", "value": "..."}   — post-click page.url check
      {"type": "selector", "value": "css..."}    — a confirmation element exists
      {"type": "text", "value": "..."}           — literal text appears on the page
    No success_check configured -> (False, explanatory reason), never a
    silent pass."""
    if not success_check:
        return False, "no verified success_check configured for this broker"
    check_type = success_check.get("type")
    value = success_check.get("value")
    try:
        if check_type == "url_contains":
            return (value in page.url), None
        if check_type == "selector":
            el = await page.query_selector(value)
            return (el is not None), None
        if check_type == "text":
            content = await page.content()
            return (value in content), None
    except Exception as exc:
        return False, f"success check raised: {exc}"
    return False, f"unrecognized success_check type: {check_type!r}"
