"""Test-only barriers: completed jobs may still be finishing their final journal."""


def cleanup_after_idle(test_case, temporary_directory, lock):
    acquired = lock.acquire(timeout=10)
    test_case.assertTrue(acquired, "Background test task did not finish its final records")
    try:
        temporary_directory.cleanup()
    finally:
        lock.release()
