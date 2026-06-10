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

def score_items(items: list[dict]) -> list[dict]:
    """
    Scores a list of briefing items for relevance to apologetics, theology, 
    pastoral ministry, and cultural engagement using Gemini. 
    Returns the same list with a 'gemini_score' (0-10) added to each item.
    Falls back to a score of 0 if Gemini is unavailable or fails.
    """
    if not api_key:
        # Fallback: assign 0 score if API key is not available
        for item in items:
            item['gemini_score'] = 0
        return items

    try:
        model = genai.GenerativeModel(GEMINI_MODEL_NAME)
    except Exception: # Catches errors if genai.configure failed or model not found
        for item in items:
            item['gemini_score'] = 0
        return items

    scored_items = []
    batch_size = 10

    for i in range(0, len(items), batch_size):
        batch = items[i:i + batch_size]
        prompt_items = []
        for item in batch:
            prompt_items.append({"title": item.get('title', ''), "summary": item.get('summary', '')})

        if not prompt_items:
            # Empty batch, just skip
            continue

        prompt = f"""
        You are an expert content analyzer. Given a list of briefing items, score each item from 0 to 10 for its relevance to the combined fields of apologetics, theology, pastoral ministry, and cultural engagement.

        Your response MUST be a JSON array of integers, where each integer is the score for the corresponding item in the input list. Do NOT include any other text or formatting. Each score must be between 0 and 10, inclusive.

        Example: If the input is two items, and they score 7 and 4, your output should be: [7, 4]

        Here are the items to score (titles and summaries):
        {json.dumps(prompt_items, indent=2)}
        """

        try:
            response = model.generate_content(prompt)
            scores_str = response.text.strip()
            
            # Gemini might return markdown JSON, need to strip it if present
            if scores_str.startswith("```json") and scores_str.endswith("```"):
                scores_str = scores_str[7:-3].strip()
            
            scores = json.loads(scores_str)

            if isinstance(scores, list) and all(isinstance(s, int) and 0 <= s <= 10 for s in scores) and len(scores) == len(batch):
                for j, item in enumerate(batch):
                    item['gemini_score'] = scores[j]
            else:
                # Malformed response or scores out of range, fallback to 0 for this batch
                for item in batch:
                    item['gemini_score'] = 0

        except Exception as e:
            # print(f"Error calling Gemini for relevance scoring: {e}") # For debugging
            for item in batch:
                item['gemini_score'] = 0
        finally:
            scored_items.extend(batch)

    return scored_items
