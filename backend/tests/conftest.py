from collections.abc import Iterator
from pathlib import Path

import pytest

from app.tools.sql import Database

SALES_CSV = """order_id,region,product,revenue,order_date
1,North,Widget,120.5,2024-01-05
2,South,Gadget,80.0,2024-01-17
3,North,Gadget,,2024-02-02
4,East,Widget,200.0,2024-02-20
5,South,Widget,50.25,2024-03-11
6,East,Gizmo,310.0,2024-03-28
"""


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "data"
    d.mkdir()
    (d / "sales.csv").write_text(SALES_CSV)
    return d


@pytest.fixture
def db(data_dir: Path) -> Iterator[Database]:
    database = Database(data_dir)
    yield database
    database.close()
