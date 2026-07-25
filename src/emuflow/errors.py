class EmuFlowError(Exception):
    """Base class for actionable user-facing flow errors."""


class ValidationError(EmuFlowError):
    """Raised when a versioned artifact violates its schema invariants."""


class ImportError(EmuFlowError):
    """Raised when an external tool artifact cannot be imported."""
