"""The option-structure taxonomy — which side the risk is on, and how many legs.

This is **vocabulary, not policy**: it answers "what shape is this position",
never "what should we do about it". The trader's rules live in
``config/trade_mgmt.toml`` (see ``shared/trade_mgmt.py``); the shapes here are
facts about the structures themselves, so they are code rather than config.

**It exists because the same three sets had reached SEVEN copies across tiers
that cannot import each other** — ``options-scanner`` engines and
``services/options_svc`` — and one of those copies was wrong:
``paper_adjust.apply_roll`` tested ``strategy in ("PCS", "IC")`` to pick the
option right, so a short put resolved to ``"CALL"`` and its roll would have been
priced off the call chain. It was unreachable only because single-leg positions
had no roll candidate to apply. The full census is in
docs/plans/2026-09-11-income-exit-rules-design.md §5.

⚠ Input is NORMALISED (stripped + upper-cased). ``paper_engine`` upper-cased
before testing and ``rescue`` did not, so the two genuinely disagreed about
``"short_put"``. One answer now, and it is the permissive one.
"""

# The Income Window's cash-secured put. TWO spellings for ONE structure -
# SHORT_PUT on the scan side, NAKED_PUT on the Calculator/rescue side - and both
# can sit in the paper book, so anything keyed on one must accept the other.
SHORT_PUT = ("SHORT_PUT", "NAKED_PUT")

# The Income Window's covered call. A tuple rather than a bare string because
# the short put's history says a second spelling is a matter of time.
COVERED_CALL = ("COVERED_CALL",)

# ONE short option, not a spread: the two structures the Income Window opens.
SINGLE_LEG = SHORT_PUT + COVERED_CALL

# Put-side structures: the danger is the underlying FALLING toward the short
# strike. An iron condor is BOTH sides and counts as put-side here because
# ``short_strike`` holds its put short - the field every proximity test reads.
PUT_SIDE = ("PCS", "IC") + SHORT_PUT

# How many option legs it takes to CLOSE the structure. An iron condor is four,
# because it is a put spread AND a call spread; the income structures are one.
_LEGS = {"IC": 4, **{s: 1 for s in SINGLE_LEG}}

# What an unrecognised structure costs to close. Commissions are the consumer,
# and UNDER-billing an unknown structure makes an action look cheaper than it is
# against the alternatives it is ranked against - so the default is the spread.
_DEFAULT_LEGS = 2


def normalise(strategy) -> str:
    """A structure name in canonical form; ``""`` for anything absent."""
    return str(strategy or "").strip().upper()


def canonical(strategy) -> str:
    """One name per STRUCTURE, collapsing alternate spellings.

    ``config/trade_mgmt.toml``'s ``[structures.*]`` tables are keyed on this, so
    the short put's two spellings cannot be handed different exit rules by
    someone editing one table and not noticing the other. Anything with a single
    spelling comes back normalised and otherwise untouched.
    """
    name = normalise(strategy)
    if name in SHORT_PUT:
        return SHORT_PUT[0]
    if name in COVERED_CALL:
        return COVERED_CALL[0]
    return name


def is_short_put(strategy) -> bool:
    """A cash-secured / naked short put, in either spelling."""
    return normalise(strategy) in SHORT_PUT


def is_covered_call(strategy) -> bool:
    return normalise(strategy) in COVERED_CALL


def is_single_leg(strategy) -> bool:
    """One short option — priced off that leg's own market, closed as one leg."""
    return normalise(strategy) in SINGLE_LEG


def is_put_side(strategy) -> bool:
    """True when the position's risk is to the DOWNSIDE."""
    return normalise(strategy) in PUT_SIDE


def short_right(strategy) -> str:
    """``"PUT"`` or ``"CALL"`` for the structure's SHORT leg — the option right a
    roll or a close has to be priced on. An iron condor answers ``"PUT"``, which
    is the same put-side approximation ``rescue.build_roll_out`` already warns
    about on its candidate."""
    return "PUT" if is_put_side(strategy) else "CALL"


def option_legs(strategy) -> int:
    """Option legs it takes to close the position — the commission leg count."""
    return _LEGS.get(normalise(strategy), _DEFAULT_LEGS)
