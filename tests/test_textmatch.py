from fitcheck.textmatch import appears_in, names_term, normalize


def test_spaces_line_breaks_and_case_are_ignored():
    assert appears_in("Experience  with\nPYTHON", "experience with python")


def test_curly_quotes_match_straight_ones():
    # The bug from the first eval run: postings use ’, Haiku types '.
    assert appears_in("Pursuing a bachelor's degree", "Pursuing a bachelor’s degree")
    assert appears_in("“self-starter”", 'a "self-starter" attitude')


def test_dashes_ellipsis_and_non_breaking_spaces():
    assert appears_in("Dec 2028 - Spring 2029", "grad date: Dec 2028 – Spring 2029")
    assert appears_in("tools-like", "tools—like")
    assert appears_in("and more...", "and more…")
    assert appears_in("3+ years", "3+ years")


def test_other_characters_still_must_match():
    assert not appears_in("Python 3", "Python 2")
    assert not appears_in("C#", "C++")


def test_normalize_only_compares():
    assert normalize("Dec 2028 – Spring") == "dec 2028 - spring"


def test_names_term_whole_words():
    assert names_term("Built in Go", "go")
    assert not names_term("Good tooling", "go")
