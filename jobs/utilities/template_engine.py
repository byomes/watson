"""jobs/utilities/template_engine.py — Render Jinja2 templates from strings or files."""
import logging

log = logging.getLogger(__name__)


def render(template_str: str, context: dict) -> str:
    from jinja2 import Template
    try:
        return Template(template_str).render(**context)
    except Exception as exc:
        log.error("template render failed: %s", exc)
        return f"Template error: {exc}"


def render_file(template_path: str, context: dict) -> str:
    from jinja2 import Environment, FileSystemLoader
    import os
    try:
        directory = os.path.dirname(os.path.abspath(template_path))
        filename = os.path.basename(template_path)
        env = Environment(loader=FileSystemLoader(directory))
        tmpl = env.get_template(filename)
        return tmpl.render(**context)
    except Exception as exc:
        log.error("template render_file failed: %s", exc)
        return f"Template error: {exc}"


def run(message: str = None) -> str:
    return "Template engine ready."
