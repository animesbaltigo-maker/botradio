from __future__ import annotations


class LocalizedError(RuntimeError):
    def __init__(self, key: str, **params: object) -> None:
        super().__init__(key)
        self.key = key
        self.params = params

    def __str__(self) -> str:
        return self.key

