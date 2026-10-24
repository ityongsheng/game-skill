class GameKBError(RuntimeError):
    """Base error for game-kb."""


class ValidationError(GameKBError):
    """Input or generated evidence failed validation."""


class ExtractionError(GameKBError):
    """A deterministic extractor could not process a source."""


class PublishError(GameKBError):
    """A release step failed; local state is retained for retry."""
