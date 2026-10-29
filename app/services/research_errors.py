class ResearchError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False, status: int = 502):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.status = status
