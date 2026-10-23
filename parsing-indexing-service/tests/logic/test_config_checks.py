from types import SimpleNamespace

import pytest
from app.logic.config_checks import require_settings


def test_all_present_passes():
    require_settings(SimpleNamespace(TYPE="x", A="1", B="2"), "TYPE", "A", "B")


def test_the_unset_and_empty_ones_are_named_with_the_selector_that_needs_them():
    with pytest.raises(ValueError, match=r"^TYPE='x' requires B, C\.$"):
        require_settings(SimpleNamespace(TYPE="x", A="1", B="", C=None), "TYPE", "A", "B", "C")
    with pytest.raises(ValueError, match="requires MISSING"):
        require_settings(SimpleNamespace(TYPE="x"), "TYPE", "MISSING")
