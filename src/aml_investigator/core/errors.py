class DomainError(Exception):
    pass


class NotFoundError(DomainError):
    def __init__(self, kind: str, key: object) -> None:
        super().__init__(f"{kind} {key} does not exist")
        self.kind = kind
