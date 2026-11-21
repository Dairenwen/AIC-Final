from innovation_mining.utils import split_list


def test_split_list_keeps_unique_values() -> None:
    assert split_list("alpha, beta, alpha") == ["alpha", "beta"]
