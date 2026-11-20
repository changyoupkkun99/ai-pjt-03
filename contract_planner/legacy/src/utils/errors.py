class ServiceError(Exception):
    """Only these controlled, non-sensitive messages may be shown in the UI."""


class AuthenticationError(ServiceError):
    pass


class ResponseFormatError(ServiceError):
    pass
