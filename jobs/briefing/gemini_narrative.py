import os
import json
import google.generativeai as genai

# --- Configuration for Gemini ---
api_key = os.environ.get("GEMINI_API_KEY") or \
          os.environ.get("GOOGLE_AI_STUDIO_API_KEY") or \
          os.environ.get("GOOGLE_API_KEY")

if api_key:
    genai.configure(api_key=api_key)

GEMINI_MODEL_NAME = "gemini-3.5-flash"

def generate_narrative(items: list[dict]) -> str:
    """
    Generates a 2-3 sentence daily introduction paragraph based on the titles
    and summaries of top briefing items using Gemini.
    Returns an empty string on failure.
    """
    if not api_key:
        return "" # Return empty string if API key is not available

    try:
        model = genai.GenerativeModel(GEMINI_MODEL_NAME)
    except Exception: # Catches errors if genai.configure failed or model not found
        return ""

    narrative_items_data = []
    # Take up to the top 5 items for narrative generation
    # The items list is assumed to be ordered by importance/relevance already.
    for item in items[:5]:
        narrative_items_data.append(f"Title: {item.get('title', '')}\nSummary: {item.get('summary', '')}")

    if not narrative_items_data:
        return ""

    prompt_items_str = "\n---\n".join(narrative_items_data)

    prompt = f"""
    You are an AI assistant creating a daily briefing introduction.
    Based on the following briefing items, generate a concise 2-3 sentence introductory paragraph that highlights key themes, interesting points, or areas worth attention today. Focus on themes relevant to apologetics, theology, pastoral ministry, and cultural engagement.

    Here are the briefing items:
    {prompt_items_str}
    """

    try:
        response = model.generate_content(prompt)
        narrative = response.text.strip()
        return narrative
    except Exception as e:
        # print(f"Error calling Gemini for narrative generation: {e}") # For debugging
        return ""
