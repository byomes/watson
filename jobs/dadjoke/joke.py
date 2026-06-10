"""Dad joke skill — returns a random dad joke."""

import random

_JOKES = [
    "Why don't skeletons fight each other? They don't have the guts.",
    "I'm reading a book about anti-gravity. It's impossible to put down.",
    "Why did the scarecrow win an award? He was outstanding in his field.",
    "I used to play piano by ear, but now I use my hands.",
    "What do you call a fake noodle? An impasta.",
    "Why don't eggs tell jokes? They'd crack each other up.",
    "I only know 25 letters of the alphabet. I don't know y.",
]


def get_dad_joke():
    """Return a randomly selected dad joke from the hardcoded list."""
    return random.choice(_JOKES)
