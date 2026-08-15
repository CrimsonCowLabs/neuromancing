from app.seed import _reseed_strategy_ids


def test_reseed_refreshes_non_evolved_agent():
    # Agent still on its house seed strategies -> a reseed refreshes to seed defaults
    # (so roster/catalog changes propagate).
    owner = {1: "house", 2: "house", 5: "house", 6: "house"}
    out = _reseed_strategy_ids(current_ids=[1, 2], seed_ids=[5, 6], owner_type_by_id=owner)
    assert out == [5, 6]


def test_reseed_preserves_adopted_evolution():
    # Agent adopted a self-evolved (owner_type='user') strategy -> a reseed must NOT
    # revert it. Its current lineage is preserved untouched.
    owner = {1: "house", 9: "user", 5: "house", 6: "house"}
    out = _reseed_strategy_ids(current_ids=[1, 9], seed_ids=[5, 6], owner_type_by_id=owner)
    assert out == [1, 9]


def test_reseed_unknown_ids_treated_as_house():
    # Missing owner info defaults to house (refresh) — never accidentally "evolved".
    out = _reseed_strategy_ids(current_ids=[1, 2], seed_ids=[7], owner_type_by_id={})
    assert out == [7]
