from app.auth.rate_limit import check_rate_limit, reset_rate_limit


def test_rate_limit_blocks_after_max():
    key = "test-ip-1"
    reset_rate_limit(key)
    for _ in range(5):
        assert check_rate_limit(key, max_attempts=5, window_seconds=60) is True
    assert check_rate_limit(key, max_attempts=5, window_seconds=60) is False
    reset_rate_limit(key)


def test_rate_limit_allows_different_keys():
    reset_rate_limit("a")
    reset_rate_limit("b")
    assert check_rate_limit("a", max_attempts=1, window_seconds=60) is True
    assert check_rate_limit("b", max_attempts=1, window_seconds=60) is True