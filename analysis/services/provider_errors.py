"""Safe provider diagnostics: never log remote bodies, credentials or source text."""
class AnalysisProviderError(RuntimeError):
    def __init__(self, code, message, retryable=False):
        self.code = code
        self.retryable = retryable
        super().__init__(message)
