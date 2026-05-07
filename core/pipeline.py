import logging
import sys
from datetime import datetime

log = logging.getLogger(__name__)


def _ts():
    return datetime.now().strftime("%H:%M:%S")


def _step(name, fn):
    log.info("[%s] %-30s ...", _ts(), name)
    try:
        result = fn()
        log.info("[%s] %-30s OK — %s", _ts(), name, result)
        return result, None
    except Exception as e:
        log.error("[%s] %-30s FAILED — %s", _ts(), name, e)
        return None, e


def run():
    from core.fetcher import fetch_all
    from core.summarizer import summarize_items
    from briefing.builder import build
    from briefing.publisher import push

    log.info("[%s] === Watson pipeline starting ===", _ts())

    fetched, err = _step("fetch", fetch_all)
    if err:
        fetched = 0

    summarized, err = _step("summarize", summarize_items)
    if err:
        summarized = 0

    _, err = _step("build briefing", build)
    build_ok = err is None

    deployed = False
    if build_ok:
        pushed, err = _step("publish", push)
        deployed = err is None and bool(pushed)
    else:
        log.warning("[%s] %-30s SKIPPED (build failed)", _ts(), "publish")

    summary = {
        "items_fetched": fetched or 0,
        "items_summarized": summarized or 0,
        "deployed": deployed,
    }

    log.info(
        "[%s] === Pipeline complete — fetched=%d summarized=%d deployed=%s ===",
        _ts(),
        summary["items_fetched"],
        summary["items_summarized"],
        summary["deployed"],
    )
    return summary


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
    )
    result = run()
    print(f"\nResult: {result}")
    sys.exit(0 if result["deployed"] else 1)
