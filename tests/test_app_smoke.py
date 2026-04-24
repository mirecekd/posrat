"""Smoke tests for the NiceGUI application bootstrap.

Phase 3 keeps UI assertions intentionally minimal: we only assert that the
module imports without side effects and that the public helper surface is
present. Live UI tests (NiceGUI ``user`` fixture) are deferred to later
phases where there is real UI logic worth exercising.
"""

from __future__ import annotations

import posrat.app as app_module


def test_app_module_exports_main() -> None:
    """``posrat.app`` must expose a callable ``main`` entry point."""

    assert callable(app_module.main)


def test_app_module_exposes_title_constant() -> None:
    """The window/tab title is a module-level constant used by NiceGUI."""

    assert app_module.APP_TITLE == "POSRAT"


def test_main_is_reexported_from_package_main() -> None:
    """``python -m posrat`` dispatches to ``posrat.app.main`` by default.

    Phase 10 added a CLI dispatcher in ``posrat.__main__`` (so operators
    can run ``python -m posrat create-admin <user>`` without booting
    the server). ``dunder_main.main`` is therefore *no longer identical*
    to ``app_module.main`` — it is a wrapper that delegates to it when
    called with no arguments. The test now asserts that relationship:
    the wrapper is callable and its default behaviour still lands on
    ``app.main`` via the ``run_server`` alias imported inside the
    dispatcher.
    """

    import posrat.__main__ as dunder_main

    assert callable(dunder_main.main)
    # The dispatcher imports app.main under the ``run_server`` alias;
    # identity check keeps the wiring honest without pinning the
    # wrapper implementation.
    assert dunder_main.run_server is app_module.main


def test_header_and_home_render_helpers_exist() -> None:
    """Step 3.2 adds a shared top-menu helper plus the home content helper."""

    assert callable(app_module._render_header)
    assert callable(app_module._render_home)


def test_menu_navigation_helpers_exist() -> None:
    """Menu entries delegate to named navigation / dialog helpers."""

    assert callable(app_module._navigate_to_designer)
    assert callable(app_module._navigate_to_runner)
    assert callable(app_module._show_about_dialog)


def test_designer_and_runner_route_constants() -> None:
    """Step 3.3 exposes the route paths as module-level constants."""

    assert app_module.DESIGNER_ROUTE == "/designer"
    assert app_module.RUNNER_ROUTE == "/runner"


def test_designer_and_runner_render_helpers_exist() -> None:
    """Each page has a pure render helper callable by unit tests."""

    assert callable(app_module._render_designer)
    assert callable(app_module._render_runner)


def test_designer_and_runner_page_handlers_exist() -> None:
    """The ``@ui.page`` handlers are module-level callables too."""

    assert callable(app_module._designer_page)
    assert callable(app_module._runner_page)


def test_dark_mode_storage_key_constant() -> None:
    """Step 3.4 exposes the storage key used to persist the dark preference."""

    assert app_module.STORAGE_KEY_DARK_MODE == "dark_mode"


def test_storage_secret_env_constant() -> None:
    """Step 3.4 lets the user override the storage secret via an env var."""

    assert app_module.STORAGE_SECRET_ENV == "POSRAT_STORAGE_SECRET"


def test_resolve_storage_secret_uses_env_when_set(monkeypatch) -> None:
    """When the env var is set, ``_resolve_storage_secret`` returns it verbatim."""

    monkeypatch.setenv(app_module.STORAGE_SECRET_ENV, "fixed-secret-value")

    assert app_module._resolve_storage_secret() == "fixed-secret-value"


def test_resolve_storage_secret_generates_random_when_missing(monkeypatch) -> None:
    """Without the env var we get a non-empty random secret each call."""

    monkeypatch.delenv(app_module.STORAGE_SECRET_ENV, raising=False)

    first = app_module._resolve_storage_secret()
    second = app_module._resolve_storage_secret()

    assert first
    assert second
    # Extremely unlikely to collide with 256 bits of entropy.
    assert first != second


def test_designer_module_exposes_render_designer() -> None:
    """Step 4.1.a moves the Designer body into :mod:`posrat.designer`."""

    from posrat.designer import render_designer

    assert callable(render_designer)


def test_app_designer_wrapper_delegates_to_designer_package() -> None:
    """``_render_designer`` in ``posrat.app`` is a thin wrapper over the package."""

    from posrat.designer import render_designer

    # The wrapper must reference the same callable imported from the package
    # so the page handler keeps pointing at the real Designer renderer.
    assert app_module.render_designer is render_designer


def test_no_browser_env_constant() -> None:
    """The env var name is part of the contract — guard the string value."""

    assert app_module.NO_BROWSER_ENV == "POSRAT_NO_BROWSER"


def test_resolve_show_browser_defaults_to_true(monkeypatch) -> None:
    """Unset env var keeps NiceGUI's default auto-open behaviour."""

    monkeypatch.delenv(app_module.NO_BROWSER_ENV, raising=False)
    assert app_module._resolve_show_browser() is True


def test_resolve_show_browser_empty_string_defaults_to_true(monkeypatch) -> None:
    """Empty value is treated as "unset" — same as no env var at all."""

    monkeypatch.setenv(app_module.NO_BROWSER_ENV, "")
    assert app_module._resolve_show_browser() is True


def test_resolve_show_browser_honours_truthy_values(monkeypatch) -> None:
    """Any of 1 / true / yes / on (case-insensitive) disables auto-open."""

    for value in ("1", "true", "TRUE", "yes", "Yes", "on", "  1  "):
        monkeypatch.setenv(app_module.NO_BROWSER_ENV, value)
        assert app_module._resolve_show_browser() is False, f"{value!r} should opt out"


def test_resolve_show_browser_ignores_other_values(monkeypatch) -> None:
    """Values outside the whitelist keep the default (open the browser).

    We deliberately don't honour things like ``0`` / ``no`` / ``false`` for
    the *opt-out* reading — the env var is strictly additive ("set to a
    truthy value to suppress"), not a toggle. Anything unexpected falls
    back to the default so a typo can't accidentally silence the browser.
    """

    for value in ("0", "no", "false", "off", "maybe", "random"):
        monkeypatch.setenv(app_module.NO_BROWSER_ENV, value)
        assert app_module._resolve_show_browser() is True, f"{value!r} should open"


def test_render_header_signature_accepts_optional_current_user() -> None:
    """Step 10.17: ``_render_header`` accepts ``current_user_display=None``.

    The callable gates the nav-button block on that argument, so the
    signature must stay optional. Live UI behaviour can't be exercised
    without a NiceGUI ``app.storage.user`` context, but the public
    contract is still checked here.
    """

    import inspect

    signature = inspect.signature(app_module._render_header)
    assert "current_user_display" in signature.parameters
    assert signature.parameters["current_user_display"].default is None
    assert "show_admin_link" in signature.parameters


def test_render_header_gates_nav_buttons_on_authentication() -> None:
    """Step 10.17: unauthenticated visitors see only the APP_TITLE.

    We scan the ``_render_header`` source for the ``authenticated``
    predicate and make sure every navigation / chrome element sits
    inside an ``if authenticated:`` branch. This is a structural guard
    against a future edit that accidentally exposes Designer / Runner /
    Admin / the dark-mode switch / the About dialog to anonymous
    visitors on ``/login``. A live NiceGUI fixture would be stronger
    but requires ``app.storage.user`` context.
    """

    import inspect

    source = inspect.getsource(app_module._render_header)
    assert "authenticated = bool(current_user_display)" in source
    guard_idx = source.find("if authenticated:")
    assert guard_idx != -1, "missing `if authenticated:` block"
    # Use concrete ``ui.button(``/``ui.switch(`` call strings so the check
    # doesn't trip over the label names that also appear in the
    # docstring (``"Log out"`` / ``"About"`` are mentioned for humans
    # explaining the 10.17 rule).
    for needle in (
        'ui.button("Designer"',
        'ui.button("Runner"',
        'ui.button(\n                    "Admin"',
        'ui.switch("Dark mode"',
        'ui.button("About"',
        'ui.button("Log out"',
    ):
        occurrence = source.find(needle)
        assert occurrence > guard_idx, (
            f"{needle!r} must sit inside the authenticated-only branch"
        )
