def draft_post(title, summary, url):
    """Draft a simple Facebook post from article data."""
    post = f"{title}\n\n{summary}\n\n{url}"
    return post
