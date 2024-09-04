class Fail(BaseException):
    """
    Failure that causes the program to exit with an error message.

    No stack trace is printed.
    """


class Success(BaseException):
    """
    Cause the program to exit successfully
    """
