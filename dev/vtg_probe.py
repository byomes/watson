"""One-off probe: capture the real network request vacationstogo.com's
custom.cfm search form fires on submit. Not part of the scraper itself."""
from playwright.sync_api import sync_playwright
import json

CAPTURED = []

def handle_request(request):
    CAPTURED.append({
        "url": request.url,
        "method": request.method,
        "headers": dict(request.headers),
        "post_data": request.post_data,
    })

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.on("request", handle_request)

    page.goto("https://www.vacationstogo.com/custom.cfm", wait_until="networkidle")

    # Dump form field names/ids present on the page for sanity check
    fields = page.eval_on_selector_all(
        "form input, form select",
        "els => els.map(e => ({tag: e.tagName, name: e.name, id: e.id, type: e.type, value: e.value}))"
    )
    print("=== FORM FIELDS ===")
    print(json.dumps(fields, indent=2))

    # Fill departure ports
    for i, code in enumerate(["362", "577", "1509", "1508"]):
        try:
            page.select_option(f"select[name='d{i+1}']" if i > 0 else "select[name='d']", code)
        except Exception as e:
            print(f"d slot {i}: {e}")

    CAPTURED.clear()  # only care about what fires after clicking submit
    try:
        page.click("#fabShowMeTheDeals", timeout=5000)
    except Exception as e:
        print(f"click failed: {e}")

    page.wait_for_timeout(4000)

    print("=== CAPTURED REQUESTS AFTER SUBMIT ===")
    for req in CAPTURED:
        print(json.dumps(req, indent=2))

    print("=== FINAL URL ===")
    print(page.url)
    print("=== PAGE TITLE ===")
    print(page.title())

    browser.close()
