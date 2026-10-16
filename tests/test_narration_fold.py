"""Narration must not shift onto the wrong step when the scrubber drops steps."""

from navigator.automation.narration import Segment, align, fold_lines_to_kept


def test_dropped_step_does_not_shift_lines():
    # login, email, password, show_password (dropped), go_to_home
    times = [10_000, 20_000, 30_000, 30_050, 40_000]
    segs = [
        Segment(9_000, 11_000, "Click login."),
        Segment(19_000, 21_000, "Enter the email."),
        Segment(29_000, 30_040, "Enter the password."),
        Segment(30_100, 31_000, "Reveal it."),
        Segment(39_000, 41_000, "Back home."),
    ]
    full = align(segs, times)
    kept = fold_lines_to_kept(full, [0, 1, 2, 4], times)
    assert len(kept) == 4
    assert "login" in kept[0].lower()
    assert "email" in kept[1].lower()
    assert "password" in kept[2].lower()
    assert "home" in kept[3].lower()
    # The dropped step's words land on the nearest kept step, not the next one.
    assert "reveal" in kept[2].lower() and "reveal" not in kept[3].lower()


def test_fold_keeps_order_and_handles_empty():
    assert fold_lines_to_kept([], [], []) == []
    assert fold_lines_to_kept(["a", "", "c"], [0, 2], [0, 1, 2]) == ["a", "c"]
    assert fold_lines_to_kept(["", "b", ""], [0, 2], [0, 10, 100]) == ["b", ""]
