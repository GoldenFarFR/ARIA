from aria_core.gateway.x_twitter import is_x_configured, is_x_post_configured, is_x_read_configured, x_status


def test_x_status_defaults():
    st = x_status()
    assert st["handle"] == "@Aria_ZHC"
    assert "read" in st
    assert "post" in st
    assert is_x_configured() == (is_x_read_configured() or is_x_post_configured())