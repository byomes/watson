import os
import json
import importlib

SKILLS_FILE = "/home/billyomes/watson/memory/skills.json"

def load_skills():
    if not os.path.exists(SKILLS_FILE):
        return []
    try:
        with open(SKILLS_FILE, "r") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data.get("skills", [])
            elif isinstance(data, list):
                return data
    except Exception:
        return []
    return []

def route_message(message: str) -> str:
    """
    Routes the incoming message to the appropriate skill if matched.
    Returns the execution result or None if no skill matches.
    """
    if not message:
        return None

    msg_clean = message.lower().strip()
    skills = load_skills()

    # Direct Keyword / Phrase match
    for skill in skills:
        keywords = skill.get("keywords", []) or skill.get("triggers", []) or skill.get("phrases", [])
        for kw in keywords:
            if kw.lower().strip() in msg_clean:
                endpoint = skill.get("endpoint") or f"{skill.get('module')}.{skill.get('function')}"
                if endpoint:
                    return execute_skill(endpoint)

    return None

def execute_skill(endpoint: str) -> str:
    try:
        parts = endpoint.split(".")
        module_name = ".".join(parts[:-1])
        func_name = parts[-1]
        module = importlib.import_module(module_name)
        func = getattr(module, func_name)
        return func()
    except Exception as e:
        return f"Error executing skill {endpoint}: {str(e)}"