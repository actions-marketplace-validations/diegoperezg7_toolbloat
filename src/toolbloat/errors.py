"""User-facing errors. The CLI prints these without a traceback."""


class ToolbloatError(Exception):
    """An error with a message that tells the user what to do next."""


class MissingApiKey(ToolbloatError):
    pass


class ProviderUnreachable(ToolbloatError):
    pass


class ModelNotFound(ToolbloatError):
    pass


class BudgetExceeded(ToolbloatError):
    pass


class ServerStartError(ToolbloatError):
    pass
