"""Tests for the React Operator Console serving and static asset delivery."""

import re
from fastapi.testclient import TestClient

from app.main import app, FRONTEND_DIST_DIR, CONSOLE_HTML_PATH


def test_console_serves_built_react_app():
    """Verifies that GET / and GET /console serve the built React application."""
    client = TestClient(app)

    # Test root route
    res_root = client.get("/")
    assert res_root.status_code == 200
    assert "text/html" in res_root.headers.get("content-type", "")
    assert '<div id="root"></div>' in res_root.text

    # Test /console route
    res_console = client.get("/console")
    assert res_console.status_code == 200
    assert "text/html" in res_console.headers.get("content-type", "")
    assert '<div id="root"></div>' in res_console.text

    # Check script reference
    script_match = re.search(r'src="(/assets/index-[^"]+\.js)"', res_root.text)
    assert script_match is not None, "Built bundle script not found in HTML output"
    js_asset_path = script_match.group(1)

    # Check stylesheet reference
    css_match = re.search(r'href="(/assets/index-[^"]+\.css)"', res_root.text)
    assert css_match is not None, "Built bundle stylesheet not found in HTML output"
    css_asset_path = css_match.group(1)

    # Verify that mounted static assets serve successfully
    res_js = client.get(js_asset_path)
    assert res_js.status_code == 200
    assert len(res_js.content) > 1000

    res_css = client.get(css_asset_path)
    assert res_css.status_code == 200
    assert len(res_css.content) > 100
