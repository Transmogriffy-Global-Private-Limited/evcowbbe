from ninja.errors import HttpError

class DomainError(HttpError):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(status, message)
        self.code = code


def reject(status, code, message):
    raise DomainError(status, code, message)
