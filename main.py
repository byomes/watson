import sys
import logging
import importlib.util
from pathlib import Path

# The local telegram/ directory shadows python-telegram-bot's installed 'telegram' package.
# Moving the project root to the end of sys.path lets the installed package resolve first,
# while still allowing imports of config/, core/, briefing/, etc.
_root = str(Path(__file__).parent)
if _root in sys.path:
    sys.path.remove(_root)
sys.path.append(_root)

from dotenv import load_dotenv
load_dotenv()


def _run_bot():
    spec = importlib.util.spec_from_file_location(
        "_watson_bot",
        Path(__file__).parent / "telegram" / "bot.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.main()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.info("Starting Watson...")
    _run_bot()
