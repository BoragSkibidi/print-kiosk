import pytest

from app.pricing import price

TABLE = {"color": {"pesos": 5, "pages": 5}, "bw": {"pesos": 5, "pages": 10},
         "scan": {"pesos": 5, "pages": 5}}


@pytest.mark.parametrize(
    "kind,pages,expected", 
    [
        ("color", 1, 5), ("color", 3, 5), ("color", 5, 5), ("color", 7, 10), ("color", 11, 15),
        ("bw", 1, 5), ("bw", 10, 5), ("bw", 11, 10), ("bw", 12, 10), ("bw", 21, 15),
        ("color", 0, 0),
    ]
)
def test_price(kind, pages, expected):
    assert price(TABLE, kind, pages) == expected


def test_price_follows_config():
    table = {**TABLE, "color": {"pesos": 10, "pages": 4}}
    assert price(table, "color", 5) == 20
