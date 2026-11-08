class DomainError(Exception):
    """Raised when a business rule is broken (e.g. illegal status change).

    Views and admin translate this into a friendly message; models never
    know about HTTP.
    """
