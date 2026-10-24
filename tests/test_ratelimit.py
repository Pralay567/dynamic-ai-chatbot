import unittest

import helpers  # noqa: F401  (puts project root on sys.path)
from ratelimit import SlidingWindowLimiter


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class RateLimiterTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.limiter = SlidingWindowLimiter(3, 60.0, clock=self.clock)

    def test_allows_up_to_limit_then_blocks(self):
        for _ in range(3):
            self.assertEqual(self.limiter.check("a"), (True, 0))
        allowed, retry_after = self.limiter.check("a")
        self.assertFalse(allowed)
        self.assertEqual(retry_after, 60)

    def test_retry_after_shrinks_and_window_expires(self):
        for _ in range(3):
            self.limiter.check("a")
        self.clock.now += 45
        allowed, retry_after = self.limiter.check("a")
        self.assertFalse(allowed)
        self.assertEqual(retry_after, 15)
        self.clock.now += 15
        self.assertTrue(self.limiter.check("a")[0])

    def test_keys_are_independent(self):
        for _ in range(3):
            self.limiter.check("a")
        self.assertFalse(self.limiter.check("a")[0])
        self.assertTrue(self.limiter.check("b")[0])

    def test_zero_limit_disables(self):
        limiter = SlidingWindowLimiter(0, clock=self.clock)
        self.assertFalse(limiter.enabled)
        for _ in range(100):
            self.assertTrue(limiter.check("a")[0])

    def test_old_keys_are_pruned(self):
        limiter = SlidingWindowLimiter(5, 60.0, clock=self.clock, max_keys=3)
        for key in ("a", "b", "c"):
            limiter.check(key)
        self.clock.now += 120
        limiter.check("d")
        limiter.check("e")  # exceeds max_keys -> stale keys removed
        self.assertLessEqual(len(limiter._hits), 3)


if __name__ == "__main__":
    unittest.main()
