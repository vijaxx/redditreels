import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))

import rewrite_story as rs


def test_scrub_text_replaces_banned_word_and_reports_the_hit():
    text, hits = rs.scrub_text("that was such a shit situation")
    assert "shit" not in text.lower()
    assert "mess" in text.lower()
    assert hits  # at least one hit recorded


def test_scrub_text_is_case_insensitive():
    text, hits = rs.scrub_text("What the HELL is going on")
    assert "hell" not in text.lower()
    assert hits


def test_scrub_text_leaves_clean_text_untouched():
    clean = "My neighbor asked to borrow my car and never returned it."
    text, hits = rs.scrub_text(clean)
    assert text == clean
    assert hits == []


def test_scrub_text_collapses_whitespace_left_behind_by_a_removed_word():
    # "cock" -> "" leaves a double space where the word used to sit
    text, hits = rs.scrub_text("the rooster went cock a doodle doo")
    assert "  " not in text
