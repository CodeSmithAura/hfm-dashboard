"""Presentation layer: turns monitoring data into display-ready view models (no web framework).

Every decision about what the dashboard shows (labels, order, banners, durations) is made here, so
templates, themes or a completely different front end can be redesigned without touching logic.
The JSON API serves these models as they are (docs/ui.md).
"""
