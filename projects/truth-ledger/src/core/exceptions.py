class ARIAError(Exception):
    def __init__(
        self,
        message: str,
        code: str = "UNKNOWN_ERROR",
        details: dict | None = None,
    ):
        self.message = message
        self.code = code
        self.details = details or {}
        super().__init__(self.message)


class LedgerError(ARIAError):
    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message, "LEDGER_ERROR", details)


class LedgerFileError(LedgerError):
    def __init__(self, message: str, path: str | None = None):
        details = {"path": path} if path else {}
        super().__init__(message, details)


class ValidationError(ARIAError):
    def __init__(self, message: str, field: str | None = None):
        details = {"field": field} if field else {}
        super().__init__(message, "VALIDATION_ERROR", details)
