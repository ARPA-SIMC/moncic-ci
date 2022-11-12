from typing import Sequence


class Texttable:
    HEADER: int

    def __init__(self, max_width: int): ...
    def set_deco(self, deco: int) -> None: ...
    def add_row(self, array: Sequence[str]): ...
    def set_cols_align(self, array: Sequence[str]): ...
    def set_cols_dtype(self, array: Sequence[str]): ...
    def draw(self) -> str: ...
