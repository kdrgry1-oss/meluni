import importlib.util
from pathlib import Path
import sys
import types


# Import only the theme module under test. Importing ``routes`` normally also
# registers the complete auth/database graph, which makes these pure helper
# tests depend on unrelated production providers.
routes_dir = Path(__file__).parents[1] / "routes"
routes_pkg = types.ModuleType("theme_test_routes")
routes_pkg.__path__ = [str(routes_dir)]
sys.modules[routes_pkg.__name__] = routes_pkg

deps = types.ModuleType(f"{routes_pkg.__name__}.deps")
deps.db = object()
deps.logger = types.SimpleNamespace(info=lambda *_args, **_kwargs: None)
deps.require_admin = lambda: None
deps.require_permission = lambda _perm: (lambda: None)  # IAM-02: tema uçları tasarim.* izni ister
deps.generate_id = lambda: "test-id"
sys.modules[deps.__name__] = deps

spec = importlib.util.spec_from_file_location(
    f"{routes_pkg.__name__}.themes", routes_dir / "themes.py"
)
themes = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = themes
spec.loader.exec_module(themes)

THEME_META = themes.THEME_META
THEME_TOKENS = themes.THEME_TOKENS
_definition = themes._definition
_validate_settings = themes._validate_settings


def test_builtin_gallery_has_three_original_layouts():
    assert set(THEME_META) == {"atelier-editorial", "gallery-minimal", "nocturne-studio"}
    assert {THEME_META[key][2] for key in THEME_META} == {"editorial", "minimal", "nocturne"}
    assert all("clone" not in THEME_META[key][0].lower() for key in THEME_META)


def test_builtin_theme_uses_accessible_token_contract():
    theme = _definition("atelier-editorial")
    assert theme["settings"]["tokens"] == THEME_TOKENS["atelier-editorial"]
    assert {"background", "surface", "text", "muted", "accent", "border"} <= set(theme["settings"]["tokens"])
    assert all(block.get("id") and block.get("is_active") for block in theme["blocks"])


def test_theme_settings_drop_unknown_token_keys():
    cleaned = _validate_settings({"layout": "minimal", "tokens": {"text": "#111", "evil": "url(x)"}})
    assert cleaned["layout"] == "minimal"
    assert cleaned["tokens"] == {"text": "#111"}
