from datetime import datetime
from zoneinfo import ZoneInfo

def run(*args, **kwargs) -> str:
    """
    Returns the current system time in Eastern format (e.g., "It's 2:34 PM (Eastern)")
    """
    try:
        tz = ZoneInfo("America/New_York")
        now = datetime.now(tz)
    except Exception:
        now = datetime.now()
    
    time_str = now.strftime("%-I:%M %p")
    return f"It's {time_str} (Eastern)"