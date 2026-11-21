import time


class DeviceError(RuntimeError):
    pass


class Cancelled(DeviceError):
    pass


def check_deadline(deadline, cancel):
    if cancel.is_set():
        raise Cancelled("Operation cancelled")
    if time.monotonic() >= deadline:
        raise TimeoutError("Biometric operation timed out")
