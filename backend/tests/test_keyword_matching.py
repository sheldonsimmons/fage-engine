from core.keywords import term_matches_text


def test_nda_does_not_match_monday():
    assert not term_matches_text("nda", "The API call is scheduled for Monday.")


def test_nda_matches_as_a_complete_term_case_insensitively():
    assert term_matches_text("nda", "Please review the NDA.")
    assert term_matches_text("NDA", "This is under nda, effective today.")


def test_multi_word_terms_respect_outer_boundaries():
    assert term_matches_text("medical record", "Review the medical record.")
    assert not term_matches_text("legal", "The legalese needs simplifying.")
