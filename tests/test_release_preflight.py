from forex_ai.release_preflight import assess_release_account


def check(mode, *, bound=True, present=False, approved=False, match=False):
    return assess_release_account(
        mode=mode, bound=bound, real_approval_present=present,
        real_approval_valid=approved, real_fingerprint_matches=match,
    )


def test_demo_allows_unrelated_real_approval_drift_when_broker_bound():
    decision = check("DEMO", bound=True, present=True, approved=True, match=False)
    assert decision.permitted and decision.reason_code == "RELEASE_BROKER_VERIFIED_DEMO"


def test_demo_unbound_fails_closed():
    assert not check("DEMO", bound=False).permitted


def test_real_requires_original_approval_and_matching_fingerprint():
    assert check("REAL", present=True, approved=True, match=True).permitted
    assert not check("REAL", present=True, approved=True, match=False).permitted
    assert not check("REAL", present=False, approved=False, match=False).permitted
    assert not check("REAL", present=True, approved=False, match=True).permitted


def test_unknown_and_contest_never_sneak_through_demo():
    assert not check("UNKNOWN", bound=True).permitted
    assert not check("CONTEST", bound=True).permitted
    assert not check("REAL", bound=False, present=True, approved=True, match=True).permitted
