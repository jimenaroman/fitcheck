import pytest

from fitcheck.aliases import SKILL_ALIASES, canonical, canonicalize


def test_known_alias_is_mapped():
    assert canonical("js") == "javascript"


def test_unknown_skill_passes_through():
    assert canonical("rust") == "rust"


@pytest.mark.parametrize("name", ["java", "jsx", "tsql", "node.js developer"])
def test_exact_matches_only(name):
    assert canonical(name) == name  # "java" must never become "javascript"


def test_duplicates_merge_in_first_seen_order():
    assert canonicalize(["js", "sql", "javascript"]) == ["javascript", "sql"]


def test_table_is_clean():
    for variant, name in SKILL_ALIASES.items():
        assert variant == variant.strip().lower() and name == name.strip().lower()
        assert name not in SKILL_ALIASES  # no chains like a -> b -> c
        assert canonical(name) == name


def test_names_for_lists_canonical_name_and_variants():
    from fitcheck.aliases import names_for

    assert names_for("node.js") == ["node.js", "node", "nodejs"]
    assert names_for("rust") == ["rust"]
