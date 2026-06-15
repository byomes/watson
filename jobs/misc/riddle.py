"""Riddle skill — returns a random riddle question (answer kept internally)."""

import random

# Each entry stores both the question and its answer. Only the question is
# presented to the user; the answer is retained internally for potential future
# use (e.g. a future "reveal the answer" command).
_RIDDLES = [
    # Classic
    {"question": "What has keys but can't open locks?", "answer": "A piano."},
    {"question": "What has hands but cannot clap?", "answer": "A clock."},
    {"question": "The more you take, the more you leave behind. What am I?",
     "answer": "Footsteps."},
    {"question": "What gets wetter the more it dries?", "answer": "A towel."},
    {"question": "I have cities, but no houses; forests, but no trees; and water, "
                 "but no fish. What am I?", "answer": "A map."},
    # Word-based
    {"question": "What word becomes shorter when you add two letters to it?",
     "answer": "Short."},
    {"question": "What five-letter word becomes shorter when you add two letters "
                 "to it?", "answer": "Short (short + 'er')."},
    {"question": "What begins with T, ends with T, and has T in it?",
     "answer": "A teapot."},
    # Logic / lateral thinking
    {"question": "A man looks at a painting and says, 'Brothers and sisters I have "
                 "none, but that man's father is my father's son.' Who is in the "
                 "painting?", "answer": "His son."},
    {"question": "What can travel around the world while staying in a corner?",
     "answer": "A postage stamp."},
    {"question": "If two's company and three's a crowd, what are four and five?",
     "answer": "Nine."},
    {"question": "What has to be broken before you can use it?", "answer": "An egg."},
]


def riddle_skill(query):
    """Spec-style entry point: match riddle intent and return a result tuple.

    Returns ``(question_text, True)`` when the query asks for a riddle,
    otherwise ``(None, False)``.
    """
    if not query:
        return (None, False)
    q = query.lower()
    if "tell me a riddle" in q or "riddle" in q:
        return (random.choice(_RIDDLES)["question"], True)
    return (None, False)


def get_riddle():
    """Return a random riddle question (answer withheld)."""
    return random.choice(_RIDDLES)["question"]


def run(message: str = None) -> str:
    """Router entry point — returns a random riddle question."""
    return get_riddle()
