"""Failed-attempt backoff: per-client, global, and the bounds on both.

Like the other auth suites these pass ``now=`` explicitly and never patch a
clock. Every expectation is DERIVED from the module's constants rather than
written as a literal, so tuning ``LOCKOUT_BASE_SEC`` or ``GLOBAL_LOCKOUT_SEC``
moves the tests with the code instead of turning them red -- the same reasoning
``gex_collector`` already applies to ``LOCK_TTL_SEC``.
"""
import auth

T0 = 1_757_000_000


def test_a_fresh_client_is_not_locked():
    st = auth.LockoutState()
    assert st.locked_until("1.2.3.4", now=T0) == 0


def test_lockout_engages_after_the_threshold_and_backs_off():
    st = auth.LockoutState()
    for _ in range(auth.LOCKOUT_THRESHOLD):
        st.record_failure("1.2.3.4", now=T0)
    assert st.locked_until("1.2.3.4", now=T0) > T0


def test_a_success_clears_that_client():
    st = auth.LockoutState()
    for _ in range(auth.LOCKOUT_THRESHOLD):
        st.record_failure("1.2.3.4", now=T0)
    st.record_success("1.2.3.4")
    assert st.locked_until("1.2.3.4", now=T0) == 0


def test_one_client_being_locked_does_not_lock_another():
    st = auth.LockoutState()
    for _ in range(auth.LOCKOUT_THRESHOLD):
        st.record_failure("1.2.3.4", now=T0)
    assert st.locked_until("5.6.7.8", now=T0) == 0


def test_a_global_flood_from_rotating_addresses_still_throttles():
    """Per-IP alone is not a throttle when the attacker has a /64."""
    st = auth.LockoutState()
    for i in range(auth.GLOBAL_THRESHOLD):
        st.record_failure(f"10.0.0.{i % 256}", now=T0)
    assert st.locked_until("172.16.0.1", now=T0) > T0


def test_the_lock_expires_on_its_own():
    st = auth.LockoutState()
    for _ in range(auth.LOCKOUT_THRESHOLD):
        st.record_failure("1.2.3.4", now=T0)
    until = st.locked_until("1.2.3.4", now=T0)
    assert st.locked_until("1.2.3.4", now=until + 1) == 0


# ---------------------------------------------------------------------------
# The decision: the global lock is BRIEF, so it cannot lock the owner out.
# See the GLOBAL LOCK comment in auth.py -- these pin the trade-off so nobody
# "simplifies" the global penalty back up to LOCKOUT_MAX_SEC.


def test_the_global_lock_is_brief_not_a_quarter_of_an_hour():
    """A stranger spraying /login must not lock the owner out for 15 minutes."""
    st = auth.LockoutState()
    for i in range(auth.GLOBAL_THRESHOLD):
        st.record_failure(f"10.0.0.{i % 256}", now=T0)
    until = st.locked_until("172.16.0.1", now=T0)
    assert until <= T0 + auth.GLOBAL_LOCKOUT_SEC
    assert auth.GLOBAL_LOCKOUT_SEC < auth.LOCKOUT_MAX_SEC


def test_the_global_lock_expires_once_the_flood_stops():
    st = auth.LockoutState()
    for i in range(auth.GLOBAL_THRESHOLD):
        st.record_failure(f"10.0.0.{i % 256}", now=T0)
    assert st.locked_until("172.16.0.1",
                           now=T0 + auth.GLOBAL_LOCKOUT_SEC + 1) == 0


def test_a_brief_global_lock_never_shortens_a_longer_per_client_one():
    """The two locks compose as a MAX, not as whichever branch is tested first.

    Now that the global penalty is shorter than the per-client one, an early
    return on the global branch would hand a persistently-failing client its
    lock back EARLY -- the flood would protect the attacker.
    """
    st = auth.LockoutState()
    # Deep enough into the exponential backoff to outrun the global penalty.
    for _ in range(auth.LOCKOUT_THRESHOLD + 6):
        st.record_failure("1.2.3.4", now=T0)
    for i in range(auth.GLOBAL_THRESHOLD):
        st.record_failure(f"10.0.0.{i % 256}", now=T0)
    assert st.locked_until("1.2.3.4", now=T0) > T0 + auth.GLOBAL_LOCKOUT_SEC


# ---------------------------------------------------------------------------
# Backoff shape.


def test_the_backoff_doubles_with_each_extra_failure():
    st = auth.LockoutState()
    for _ in range(auth.LOCKOUT_THRESHOLD):
        st.record_failure("1.2.3.4", now=T0)
    first = st.locked_until("1.2.3.4", now=T0) - T0
    st.record_failure("1.2.3.4", now=T0)
    assert st.locked_until("1.2.3.4", now=T0) - T0 == first * 2


def test_the_backoff_is_capped_at_the_maximum():
    st = auth.LockoutState()
    for _ in range(auth.LOCKOUT_THRESHOLD + 40):
        st.record_failure("1.2.3.4", now=T0)
    assert st.locked_until("1.2.3.4", now=T0) == T0 + auth.LOCKOUT_MAX_SEC


def test_failures_older_than_the_window_stop_counting():
    st = auth.LockoutState()
    for _ in range(auth.LOCKOUT_THRESHOLD):
        st.record_failure("1.2.3.4", now=T0)
    assert st.locked_until("1.2.3.4", now=T0 + auth.FAILURE_WINDOW_SEC + 1) == 0


def test_a_success_really_unlocks_a_client_that_was_locked():
    """The plain ``== 0`` success test also passes when nothing locks at all.

    Assert the before-state as well, so this cannot go green against a
    ``locked_until`` that has lost its per-client branch entirely.
    """
    st = auth.LockoutState()
    for _ in range(auth.LOCKOUT_THRESHOLD):
        st.record_failure("1.2.3.4", now=T0)
    assert st.locked_until("1.2.3.4", now=T0) > T0
    st.record_success("1.2.3.4")
    assert st.locked_until("1.2.3.4", now=T0) == 0


def test_a_success_for_one_client_does_not_clear_another():
    st = auth.LockoutState()
    for _ in range(auth.LOCKOUT_THRESHOLD):
        st.record_failure("1.2.3.4", now=T0)
    st.record_success("5.6.7.8")
    assert st.locked_until("1.2.3.4", now=T0) > T0


def test_a_success_for_an_unknown_client_is_not_an_error():
    st = auth.LockoutState()
    st.record_success("never-seen")


# ---------------------------------------------------------------------------
# Memory. The box has four cores and NO SWAP; an OOM here is dropped frames on
# a public broadcast, so a rotating-source flood must not be able to grow this.


def test_reading_a_lock_does_not_create_a_client_entry():
    """``locked_until`` must not populate the defaultdict as a side effect.

    An unauthenticated GET of the login page reads this. If reading allocated,
    the read path itself would be the memory-growth vector.
    """
    st = auth.LockoutState()
    st.locked_until("1.2.3.4", now=T0)
    assert st.tracked_clients() == 0


def test_a_rotating_source_flood_cannot_grow_the_table_without_bound():
    st = auth.LockoutState()
    for i in range(auth.MAX_TRACKED_CLIENTS * 3):
        st.record_failure(f"10.{i // 65536}.{(i // 256) % 256}.{i % 256}",
                          now=T0)
    assert st.tracked_clients() <= auth.MAX_TRACKED_CLIENTS


def test_one_client_hammering_cannot_grow_its_own_list_without_bound():
    st = auth.LockoutState()
    for _ in range(5000):
        st.record_failure("1.2.3.4", now=T0)
    assert st.tracked_failures("1.2.3.4") <= auth.MAX_TRACKED_FAILURES


def test_capping_a_clients_history_does_not_change_its_backoff():
    """The cap sits at the point the exponential has already saturated.

    So truncation is invisible: 5000 failures and the smallest number that
    reaches ``LOCKOUT_MAX_SEC`` must produce the same retry instant.
    """
    hammered = auth.LockoutState()
    for _ in range(5000):
        hammered.record_failure("1.2.3.4", now=T0)
    saturated = auth.LockoutState()
    for _ in range(auth.LOCKOUT_THRESHOLD + 8):
        saturated.record_failure("1.2.3.4", now=T0)
    assert (hammered.locked_until("1.2.3.4", now=T0)
            == saturated.locked_until("1.2.3.4", now=T0)
            == T0 + auth.LOCKOUT_MAX_SEC)


def test_evicting_stale_clients_keeps_the_recent_ones():
    """Eviction drops the OLDEST last-failure, never the client attacking now."""
    st = auth.LockoutState()
    for i in range(auth.MAX_TRACKED_CLIENTS * 3):
        st.record_failure(f"10.{i // 65536}.{(i // 256) % 256}.{i % 256}",
                          now=T0 + i)
    now = T0 + auth.MAX_TRACKED_CLIENTS * 3
    for _ in range(auth.LOCKOUT_THRESHOLD):
        st.record_failure("1.2.3.4", now=now)
    assert st.locked_until("1.2.3.4", now=now) > now


def test_the_global_counter_survives_client_eviction():
    """Eviction is per-client only -- the global throttle cannot be flushed.

    A client CAN reset its own backoff by rotating through enough addresses to
    evict itself, which is precisely the case the global counter exists for.
    """
    st = auth.LockoutState()
    for i in range(auth.MAX_TRACKED_CLIENTS * 3):
        st.record_failure(f"10.{i // 65536}.{(i // 256) % 256}.{i % 256}",
                          now=T0)
    assert st.locked_until("172.16.0.1", now=T0) > T0


def test_a_global_flood_cannot_grow_the_global_list_without_bound():
    st = auth.LockoutState()
    for i in range(auth.GLOBAL_THRESHOLD * 100):
        st.record_failure(f"10.0.0.{i % 256}", now=T0)
    assert st.tracked_global() <= auth.GLOBAL_THRESHOLD


# ---------------------------------------------------------------------------
# Statefulness lives in the instance, not the module.


def test_two_states_do_not_share_anything():
    a, b = auth.LockoutState(), auth.LockoutState()
    for _ in range(auth.LOCKOUT_THRESHOLD):
        a.record_failure("1.2.3.4", now=T0)
    assert b.locked_until("1.2.3.4", now=T0) == 0
    assert b.tracked_global() == 0
