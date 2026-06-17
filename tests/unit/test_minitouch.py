"""Unit tests for minitouch coordinate scaling, swipe interpolation, and banner parsing."""

import pytest

from play_monkey.devices.minitouch import (
    MinitouchBanner,
    build_swipe_steps,
    parse_banner,
    scale,
)


class TestScale:
    def test_identity_when_touch_space_equals_display(self):
        # max coordinate maps to max coordinate
        assert scale(1080, 2340, 1080, 2340, 1080, 2340) == (1080, 2340)
        assert scale(0, 0, 1080, 2340, 1080, 2340) == (0, 0)

    def test_scales_to_larger_touch_space(self):
        # display 1080x1920 -> touch space 4096x4096
        tx, ty = scale(540, 960, 1080, 1920, 4096, 4096)
        assert tx == round(540 * 4096 / 1079)
        assert ty == round(960 * 4096 / 1919)

    def test_clamps_out_of_range(self):
        tx, ty = scale(99999, 99999, 1080, 1920, 1080, 1920)
        assert tx == 1080
        assert ty == 1920
        tx, ty = scale(-50, -50, 1080, 1920, 1080, 1920)
        assert tx == 0
        assert ty == 0

    def test_degenerate_screen_size_falls_back_to_clamp(self):
        # screen_w <= 1: no scaling, just clamp into touch range
        assert scale(500, 500, 1, 1, 1080, 2340) == (500, 500)
        assert scale(5000, 5000, 1, 1, 1080, 2340) == (1080, 2340)


class TestBuildSwipeSteps:
    def test_endpoint_is_reached(self):
        points, _ = build_swipe_steps(0, 0, 100, 200, 300)
        assert points[-1] == (100, 200)

    def test_step_count_bounds(self):
        # very short duration -> minimum 2 steps
        points, _ = build_swipe_steps(0, 0, 10, 10, 1)
        assert len(points) == 2
        # very long duration -> capped at 60 steps
        points, _ = build_swipe_steps(0, 0, 10, 10, 100000)
        assert len(points) == 60

    def test_step_sleep_sums_to_duration(self):
        duration_ms = 480
        points, step_sleep = build_swipe_steps(0, 0, 100, 100, duration_ms)
        total = step_sleep * len(points)
        assert abs(total - duration_ms / 1000.0) < 1e-9

    def test_monotonic_interpolation(self):
        points, _ = build_swipe_steps(0, 0, 90, 0, 160)
        xs = [p[0] for p in points]
        assert xs == sorted(xs)
        assert all(p[1] == 0 for p in points)


class TestParseBanner:
    def test_parses_caret_line(self):
        banner = parse_banner("v 1\n^ 10 1080 2340 255\n$ 1234\n")
        assert banner == MinitouchBanner(10, 1080, 2340, 255)

    def test_ignores_other_lines_and_whitespace(self):
        text = "  v 1 \n  ^ 2 720 1280 100 \n $ 9\n"
        banner = parse_banner(text)
        assert banner.max_contacts == 2
        assert banner.max_x == 720
        assert banner.max_y == 1280
        assert banner.max_pressure == 100

    def test_raises_without_caret_line(self):
        with pytest.raises(ValueError):
            parse_banner("v 1\n$ 1234\n")
