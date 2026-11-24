"""Which accounts a group rule takes: the public site's ``[public]`` and Discord's ``[discord]``.

A rule is ``{"enabled", "groups", "ungrouped"}``. ``"*"`` in ``groups`` is every
watched account; otherwise group names match in any case, and ``ungrouped`` adds
the accounts in no group. Read defensively: a value of the wrong type is the
shipped one, so a hand-edit can narrow a rule but never break it.
"""
ALL = "*"


def rule(raw, default) -> dict:
    """``raw`` (a config section) read against ``default`` (its shipped values)."""
    p = raw if isinstance(raw, dict) else {}
    enabled = p.get("enabled")
    ungrouped = p.get("ungrouped")
    groups = p.get("groups")
    if not (isinstance(groups, list) and all(isinstance(g, str) for g in groups)):
        groups = default["groups"]
    return {"enabled": enabled if isinstance(enabled, bool) else default["enabled"],
            "groups": [g.strip() for g in groups if g.strip()],
            "ungrouped": ungrouped if isinstance(ungrouped, bool) else default["ungrouped"]}


def matches(account, r) -> bool:
    """Whether ``account`` is one ``r`` takes."""
    if not r["enabled"]:
        return False
    if ALL in r["groups"]:
        return True
    group = account.get("group_name")
    if not group:
        return r["ungrouped"]
    return group.lower() in {g.lower() for g in r["groups"]}
