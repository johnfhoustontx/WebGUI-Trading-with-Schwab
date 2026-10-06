"""How much one entry may make this box fetch, hold and write.

Links, stylesheet bytes, files, rules, the size of the rules, time - and the
settings they are read from. Every limit here is ``config/blog.toml [fonts]``
or a constant in ``fonts.py`` that says why it is not.
"""
import pytest

from services.blog_svc import fonts
from services.blog_svc.tests._fonts_kit import (A, B, BACKSLASH, C, G, LAST_CODE_POINT, LINK,
                                                LINKS, LINK_2, NONE, SOME, Web, audit, face,
                                                name_of, one_file_blocks, rule, sheet, weights,
                                                woff2)
from shared import blog_inbox


def test_an_entry_with_no_links_asks_for_nothing():
    web = Web({})
    for nothing in ((), [], None):
        assert fonts.localize(nothing, fetch=web) == fonts.Fonts("", {}, "")
    assert web.calls == []


def test_switched_off_nothing_is_fetched_and_nothing_is_said():
    web = Web({LINK: sheet(face("latin", G + "a.woff2")), G + "a.woff2": A})
    assert fonts.localize([LINK], fetch=web, cfg={"enabled": False}) == fonts.Fonts("", {}, "")
    assert web.calls == []


@pytest.mark.parametrize("cfg", [
    {"max_files": 0}, {"max_files": 10 ** 9}, {"max_files": "many"}, {"max_files": True},
    {"max_links": -1}, {"max_css_kb": None}, {"max_file_kb": float("nan")},
    {"timeout_sec": float("inf")}, {"total_sec": [30]}, {"subsets": "latin"},
    {"subsets": []}, {"subsets": [7, None]}, {"subsets": None}, {"unknown": 1},
    {"enabled": "yes"}, {"enabled": None}, "not a dict", 7, [],
], ids=repr)
def test_a_setting_that_is_not_usable_reads_as_the_shipped_one(cfg):
    """``cfg`` is laid over the shipped settings and held to the same bounds,
    so a caller cannot switch a limit off by handing in a bad one."""
    files = {G + f"f{n}.woff2": woff2(f"f{n}") for n in range(30)}
    css = sheet(*[face("latin", url, weight=str(100 + n)) for n, url in enumerate(files)])
    web = Web({LINK: css, **files})
    result = audit(fonts.localize([LINK], fetch=web, cfg=cfg))
    shipped = blog_inbox.fonts()
    assert len(result.files) == shipped["max_files"] == 24
    assert web.calls[0]["max_bytes"] == shipped["max_css_kb"] * 1024
    assert all(call["timeout"] == shipped["timeout_sec"] for call in web.calls)


def test_a_callers_settings_are_read_exactly_as_the_files_are(monkeypatch):
    """``cfg={"timeout_sec": 9.5}`` used to read as the shipped 10 while the
    same value in the file read as 9."""
    pages = {LINK: sheet(face("latin", G + "a.woff2")), G + "a.woff2": A}
    web = Web(pages)
    audit(fonts.localize([LINK], fetch=web, cfg={"timeout_sec": 9.5}))
    assert [call["timeout"] for call in web.calls] == [9, 9]

    asked = []

    def reads(over=None, _real=blog_inbox.fonts):
        asked.append(over)
        return _real(over)
    monkeypatch.setattr(blog_inbox, "fonts", reads)
    cfg = {"timeout_sec": 9.5}
    audit(fonts.localize([LINK], fetch=Web(pages), cfg=cfg))
    assert asked == [cfg]                           # the one reading, handed the caller's table


def test_the_link_cap_holds_and_is_noted():
    links = [f"https://fonts.googleapis.com/css2?family=F{n}" for n in range(7)]
    web = Web({link: sheet(face("latin", G + f"f{n}.woff2", family=f"'F{n}'"))
               for n, link in enumerate(links)}
              | {G + f"f{n}.woff2": woff2(f"f{n}") for n in range(7)})
    result = audit(fonts.localize(links + links, fetch=web))       # a repeat is not a second link
    shipped = blog_inbox.fonts()["max_links"]
    assert shipped == 4
    assert [url for url in web.urls if "css2" in url] == links[:4]
    assert len(result.files) == 4
    assert result.note == ("Some typefaces were not copied and are shown in a fallback "
                           "font: 3 stylesheets were past the limit of 4.")


def test_a_stylesheet_over_its_size_limit_is_not_read():
    """Whatever ``fetch`` promises about ``max_bytes``, what comes back is
    measured here too: the fetch is injected."""
    cap = 16 * 1024
    block = face("latin", G + "a.woff2")
    big = sheet(block, "/*" + "x" * cap + "*/")
    web = Web({LINK: big, LINK_2: sheet(face("latin", G + "b.woff2")), G + "a.woff2": A,
               G + "b.woff2": B})
    result = audit(fonts.localize([LINK, LINK_2], fetch=web, cfg={"max_css_kb": 16}))
    assert web.calls[0]["max_bytes"] == cap
    assert web.urls == [LINK, LINK_2, G + "b.woff2"] and result.files == {name_of(B): B}
    assert "1 stylesheet could not be fetched" in result.note


def test_the_file_cap_holds_and_is_noted():
    files = {G + f"f{n}.woff2": woff2(f"f{n}") for n in range(7)}
    css = sheet(*[face("latin", url, weight=str(100 + 100 * n)) for n, url in enumerate(files)])
    web = Web({LINK: css, **files})
    result = audit(fonts.localize([LINK], fetch=web, cfg={"max_files": 3}))
    assert web.urls == [LINK] + list(files)[:3]
    assert len(result.files) == 3 and result.css.count("@font-face") == 3
    assert result.note == ("Some typefaces were not copied and are shown in a fallback "
                           "font: 4 files were past the limit of 3.")


def test_the_file_cap_counts_requests_not_successes():
    """The cap is on the work done for one entry. A stylesheet whose first
    files all fail must not get the cap again in tries."""
    files = {G + f"f{n}.woff2": (OSError("down") if n < 3 else woff2(f"f{n}")) for n in range(6)}
    css = sheet(*[face("latin", url, weight=str(100 + 100 * n)) for n, url in enumerate(files)])
    web = Web({LINK: css, **files})
    result = audit(fonts.localize([LINK], fetch=web, cfg={"max_files": 3}))
    assert len(web.calls) == 1 + 3 and result.files == {}
    assert "3 files could not be fetched" in result.note
    assert "3 files were past the limit of 3" in result.note


def test_a_file_past_the_cap_is_counted_once_however_many_blocks_name_it():
    """The note counts FILES. A variable face past the limit is one file, not
    one for each of its weights."""
    css = sheet(face("latin", G + "kept.woff2"),
                *[face("latin", G + "over.woff2", weight=weight) for weight in ("500", "600", "700")],
                face("latin", G + "other.woff2", weight="800"))
    web = Web({LINK: css, G + "kept.woff2": A, G + "over.woff2": B, G + "other.woff2": C})
    result = audit(fonts.localize([LINK], fetch=web, cfg={"max_files": 1}))
    assert web.urls == [LINK, G + "kept.woff2"] and result.css == rule(A)
    assert result.note == SOME + "2 files were past the limit of 1."


MB = 1024 * 1024


def _sized(seed, length) -> bytes:
    """A typeface file of exactly ``length`` bytes."""
    data = woff2(seed, size=length - 48)
    assert len(data) == length
    return data


@pytest.mark.parametrize("last, kept", [(MB - 800 * 1024, True), (MB - 800 * 1024 + 1, False)],
                         ids=["to the byte", "one byte over"])
def test_the_bytes_held_reach_their_limit_exactly_and_never_pass_it(last, kept):
    """Every file of one entry is in memory at once. ``max_total_mb`` is the
    most they may come to: a file that fits to the byte is kept, one that
    would make it a byte more is not - and after either, nothing more is asked
    for."""
    files = {G + "a.woff2": _sized("a", 400 * 1024), G + "b.woff2": _sized("b", 400 * 1024),
             G + "c.woff2": _sized("c", last), G + "d.woff2": _sized("d", 48),
             G + "e.woff2": _sized("e", 48)}
    css = sheet(*[face("latin", url, weight=str(100 + 100 * n)) for n, url in enumerate(files)])
    web = Web({LINK: css, **files})
    result = audit(fonts.localize([LINK], fetch=web, cfg={"max_total_mb": 1}))
    assert web.urls == [LINK] + list(files)[:3]             # d and e are never asked for
    held = sum(len(data) for data in result.files.values())
    assert held == (MB if kept else 800 * 1024) and held <= MB
    assert len(result.files) == (3 if kept else 2)
    assert result.note == SOME + (f"{2 if kept else 3} files were past the limit of 1 MB in all.")


def test_a_file_named_again_after_the_byte_limit_is_counted_once_and_not_asked_twice():
    files = {G + "a.woff2": _sized("a", 400 * 1024), G + "b.woff2": _sized("b", 400 * 1024),
             G + "c.woff2": _sized("c", 400 * 1024)}
    css = sheet(*[face("latin", url, weight=str(100 + 100 * n)) for n, url in enumerate(files)],
                face("latin", G + "c.woff2", weight="800"), face("latin", G + "a.woff2", weight="900"))
    web = Web({LINK: css, **files})
    result = audit(fonts.localize([LINK], fetch=web, cfg={"max_total_mb": 1}))
    assert web.urls == [LINK] + list(files)
    assert weights(result.css) == ["100", "200", "900"]     # a file already held is still used
    assert result.note == SOME + "1 file was past the limit of 1 MB in all."


def test_a_file_refused_for_its_bytes_has_one_reason_not_two():
    """It was asked for, weighed and not kept. When the rules later fill and
    another block names it, that block is not "past the rule limit" as well:
    its file already has its reason."""
    files = {G + "a.woff2": _sized("a", 400 * 1024), G + "b.woff2": _sized("b", 400 * 1024),
             G + "c.woff2": _sized("c", 400 * 1024)}
    css = sheet(face("latin", G + "a.woff2", weight="100"), face("latin", G + "b.woff2", weight="200"),
                face("latin", G + "c.woff2", weight="300"),         # weighed, and not kept
                face("latin", G + "a.woff2", weight="400"),         # the third rule: now full
                face("latin", G + "c.woff2", weight="500"))
    web = Web({LINK: css, **files})
    result = audit(fonts.localize([LINK], fetch=web, cfg={"max_total_mb": 1, "max_rules": 3}))
    assert weights(result.css) == ["100", "200", "400"] and web.urls == [LINK] + list(files)
    assert result.note == SOME + "1 file was past the limit of 1 MB in all."


def test_a_file_already_held_weighs_nothing_more():
    """The same bytes under a second address are one file in the result, so
    they are weighed once: with 800 KB of the megabyte used, a third address
    that turns out to be the first file again is kept, and a fourth that is
    new and too large is not."""
    again = _sized("a", 400 * 1024)
    files = {G + "a.woff2": again, G + "b.woff2": _sized("b", 400 * 1024),
             G + "mirror.woff2": bytes(again), G + "new.woff2": _sized("n", 400 * 1024)}
    css = sheet(*[face("latin", url, weight=str(100 + 100 * n)) for n, url in enumerate(files)])
    web = Web({LINK: css, **files})
    result = audit(fonts.localize([LINK], fetch=web, cfg={"max_total_mb": 1}))
    assert weights(result.css) == ["100", "200", "300"]
    assert sum(len(data) for data in result.files.values()) == 800 * 1024
    assert result.note == SOME + "1 file was past the limit of 1 MB in all."


def test_as_shipped_the_byte_limit_is_above_what_the_other_two_allow():
    """24 files of 400 KB is 9.6 MB, under the shipped 12 MB: the total binds
    only once one of the other two is raised."""
    shipped = blog_inbox.fonts()
    files = {G + f"f{n}.woff2": _sized(f"f{n}", shipped["max_file_kb"] * 1024)
             for n in range(shipped["max_files"])}
    css = sheet(*[face("latin", url, weight=str(100 + n)) for n, url in enumerate(files)])
    result = audit(fonts.localize([LINK], fetch=Web({LINK: css, **files})))
    assert len(result.files) == 24 and result.note == ""
    assert sum(len(data) for data in result.files.values()) <= shipped["max_total_mb"] * MB


def test_a_count_is_only_ever_kept_under_a_reason():
    """The other half of ``test_every_reason_has_its_words...``: the place a
    count is MADE refuses a name that is not on the list, where it happens."""
    copy = fonts._Copy(Web({}), blog_inbox.fonts(), lambda: 0.0)
    copy._miss(fonts.Reason.FILE)
    copy._miss(fonts.Reason.FILE, 2)
    assert copy.missed == {fonts.Reason.FILE: 3}
    for stray in ("file", "FILE", None, 7):
        with pytest.raises(TypeError):
            copy._miss(stray)
    assert copy.missed == {fonts.Reason.FILE: 3}


def test_the_rule_cap_holds_and_is_noted():
    web = Web({LINK: sheet(one_file_blocks("F", 10)), G + "one.woff2": A})
    result = audit(fonts.localize([LINK], fetch=web, cfg={"max_rules": 4}))
    assert weights(result.css) == ["1", "2", "3", "4"]         # the first four, in order
    assert result.files == {name_of(A): A}
    assert result.note == SOME + "6 typeface rules were past the limit of 4."


def test_five_thousand_blocks_naming_one_file_are_ninety_six_rules():
    """The measurement that found it: four stylesheets, each inside the shipped
    256 KB, 5,088 distinct blocks between them and ONE file. It came back as
    5,088 rules and 2.3 MB of CSS with nothing in the note."""
    sheets = {link: sheet(one_file_blocks(f"F{n}", 1272)) for n, link in enumerate(LINKS)}
    shipped = blog_inbox.fonts()
    assert all(len(body) <= shipped["max_css_kb"] * 1024 for body in sheets.values())
    web = Web({**sheets, G + "one.woff2": A})
    result = audit(fonts.localize(LINKS, fetch=web))
    assert result.css.count("@font-face") == shipped["max_rules"] == 96
    assert result.files == {name_of(A): A}
    assert web.urls == LINKS + [G + "one.woff2"]
    assert result.note == SOME + "4992 typeface rules were past the limit of 96."
    assert len(result.css) < 16 * 1024


def test_a_rule_past_the_cap_costs_no_request():
    """Once the rules are full, a block's file is not even asked for."""
    files = {G + f"f{n}.woff2": woff2(f"f{n}") for n in range(6)}
    css = sheet(*[face("latin", url, weight=str(100 + 100 * n)) for n, url in enumerate(files)])
    web = Web({LINK: css, **files})
    result = audit(fonts.localize([LINK], fetch=web, cfg={"max_rules": 2}))
    assert web.urls == [LINK] + list(files)[:2]
    assert result.note == SOME + "4 typeface rules were past the limit of 2."


def test_a_rule_already_written_is_not_counted_against_the_cap():
    """Two stylesheets that ask for the same face are one rule, wherever the
    second mention falls."""
    first = sheet(face("latin", G + "one.woff2", weight="400"),
                  face("latin", G + "one.woff2", weight="500"))
    second = sheet(face("latin", G + "one.woff2", weight="400"),
                   face("latin", G + "one.woff2", weight="500"),
                   face("latin", G + "one.woff2", weight="600"))
    web = Web({LINK: first, LINK_2: second, G + "one.woff2": A})
    result = audit(fonts.localize([LINK, LINK_2], fetch=web, cfg={"max_rules": 2}))
    assert weights(result.css) == ["400", "500"]
    assert result.note == SOME + "1 typeface rule was past the limit of 2."


def test_a_rule_whose_file_failed_does_not_use_up_the_cap():
    """... and is not "past the limit" either, when another block names the
    failed file after the rules are full: it has its reason already."""
    files = {G + "f0.woff2": OSError("down"), G + "f1.woff2": B, G + "f2.woff2": C}
    css = sheet(*[face("latin", url, weight=str(100 + 100 * n)) for n, url in enumerate(files)],
                face("latin", G + "f0.woff2", weight="900"))
    web = Web({LINK: css, **files})
    result = audit(fonts.localize([LINK], fetch=web, cfg={"max_rules": 2}))
    assert weights(result.css) == ["200", "300"]
    assert web.urls == [LINK] + list(files)
    assert result.note == SOME + "1 file could not be fetched or was not a typeface."


def test_a_rule_repeated_word_for_word_is_written_once():
    css = sheet(face("latin", G + "a.woff2")) * 3
    web = Web({LINK: css, LINK_2: css, G + "a.woff2": A})
    result = audit(fonts.localize([LINK, LINK_2], fetch=web))
    assert result.css == rule(A) and web.urls == [LINK, LINK_2, G + "a.woff2"]


def _largest_block(n) -> str:
    """A block that reads into the longest rule this module can write: every
    descriptor present and as long as its reader allows. ``n`` makes each one
    different without changing its length."""
    assert 1 <= n <= 999
    return face("latin", G + "one.woff2",
                family="'" + LAST_CODE_POINT * 100 + "'",          # each is written "\10ffff "
                style="oblique -89.999deg -89.999deg",
                weight=f"0{n:03d}.999 0999.999", stretch="0999.999% 0999.999%",
                display="fallback", unicode_range=", ".join(["U+100000-10FFFF"] * 128))


def test_the_size_of_the_rules_is_bounded_and_the_bound_is_exact():
    """``len(Fonts.css) <= max_rules * RULE_CHARS_CEILING + (max_rules - 1)``.

    The ceiling is worked out here from the shape of a rule, typed out, and
    must equal the module's own figure; then the largest rules there can be are
    built and come out at exactly that, so the inequality is not slack.

    What it comes to: 96 rules as shipped is under 300 KB at the very worst (a
    real rule is about a tenth of the ceiling); 1,000 rules, the limit's own
    ceiling, is 3 MB."""
    ceiling = (len("@font-face{")
               + len('font-family:""') + 100 * len(BACKSLASH + "10ffff ")
               + len(";font-style:oblique -89.999deg -89.999deg")
               + len(";font-weight:0999.999 0999.999")
               + len(";font-stretch:0999.999% 0999.999%")
               + len(";font-display:fallback")
               + len(';src:url(../fonts/' + "0" * 20 + '.woff2) format("woff2")')
               + len(";unicode-range:") + 128 * len("U+100000-10FFFF") + 127
               + len("}"))
    assert fonts.RULE_CHARS_CEILING == ceiling == 3075

    def bound(settings):
        return settings["max_rules"] * ceiling + settings["max_rules"] - 1

    web = Web({LINK: sheet(*[_largest_block(n) for n in range(1, 8)]), G + "one.woff2": A})
    result = audit(fonts.localize([LINK], fetch=web, cfg={"max_rules": 5}))
    lines = result.css.split("\n")
    assert [len(line) for line in lines] == [ceiling] * 5
    assert len(result.css) == bound({"max_rules": 5})
    assert result.note == SOME + "2 typeface rules were past the limit of 5."

    shipped = blog_inbox.fonts()
    assert bound(shipped) == 96 * 3075 + 95 < 300 * 1024
    assert bound({"max_rules": blog_inbox.BOUNDS[("fonts", "max_rules")][1]}) < 3 * 1024 * 1024
    # ... and it holds for the input that found the problem, with room to spare.
    sheets = {link: sheet(one_file_blocks(f"F{n}", 1272)) for n, link in enumerate(LINKS)}
    stress = fonts.localize(LINKS, fetch=Web({**sheets, G + "one.woff2": A}))
    assert len(stress.css) <= bound(shipped)


def test_ten_thousand_blocks_cost_a_bounded_amount_of_work():
    """10,000 blocks naming 10,000 files fit in a stylesheet at the size
    limit's ceiling. The work they cost is counted, not timed: one request for
    the stylesheet, one for each file up to the file limit, and no more - the
    other 9,976 are counted in the note without being asked for."""
    css = sheet(*["/* latin */@font-face{font-family:A;font-weight:%d;"
                  "src:url(%sn%05d.woff2) format('woff2')}" % (1 + n % 1000, G, n)
                  for n in range(10000)])
    assert 512 * 1024 < len(css) < 2048 * 1024
    files = {G + "n%05d.woff2" % n: woff2(str(n)) for n in range(24)}
    web = Web({LINK: css, **files})
    result = audit(fonts.localize([LINK], fetch=web, cfg={"max_css_kb": 2048}))
    assert web.urls == [LINK] + list(files) and len(result.files) == 24
    assert result.css.count("@font-face") == 24
    assert result.note == SOME + "9976 files were past the limit of 24."
    # ... and at the shipped limit it is not read at all.
    web = Web({LINK: css, **files})
    assert audit(fonts.localize([LINK], fetch=web)).files == {} and web.urls == [LINK]


def test_the_whole_copy_has_a_time_limit():
    """``timeout_sec`` bounds one request. Twenty-four of them timing out one
    after another would hold the service for minutes, so all of an entry's
    requests share ``total_sec`` - and a request is never given longer than
    what is left of it."""
    now = [100.0]
    files = {G + f"f{n}.woff2": woff2(f"f{n}") for n in range(5)}
    web = Web({LINK: sheet(*[face("latin", url, weight=str(100 + n))
                             for n, url in enumerate(files)]), **files})

    def slow(url, **kwargs):
        answer = web(url, **kwargs)
        now[0] += 4                          # every request takes four seconds
        return answer

    result = audit(fonts.localize([LINK], fetch=slow, clock=lambda: now[0],
                                  cfg={"timeout_sec": 5, "total_sec": 10}))
    assert [call["timeout"] for call in web.calls] == [5, 5, 2]
    assert len(result.files) == 2
    assert result.note == ("Some typefaces were not copied and are shown in a fallback "
                           "font: the time allowed for copying ran out.")


@pytest.mark.parametrize("left, asked", [(0.5, True), (0.6, True), (0.49, False), (0.001, False),
                                         (0.0, False), (-3.0, False)])
def test_a_sliver_of_time_is_no_time(left, asked):
    """With a microsecond left, a request would go out with a timeout of a
    microsecond, fail, and be reported as "could not be fetched" - the
    network's fault, when it was the clock's. Less than half a second is not
    enough for a request, so it is not made, and what is said is the truth."""
    now = [0.0]
    pages = {LINK: sheet(face("latin", G + "a.woff2")), G + "a.woff2": A}
    web = Web(pages)

    def takes_all_but(url, **kwargs):
        answer = web(url, **kwargs)
        now[0] = 10.0 - left                 # the stylesheet leaves this much for the file
        return answer

    result = audit(fonts.localize([LINK], fetch=takes_all_but, clock=lambda: now[0],
                                  cfg={"timeout_sec": 5, "total_sec": 10}))
    assert web.urls == ([LINK, G + "a.woff2"] if asked else [LINK])
    assert result.note == ("" if asked else NONE + "the time allowed for copying ran out.")
    if asked:
        assert web.calls[1]["timeout"] == pytest.approx(left)


def test_the_same_links_and_bytes_give_the_same_result_byte_for_byte():
    css = sheet(face("latin-ext", G + "e.woff2", weight="100 900", stretch="100%"),
                face("latin", G + "l.woff2", weight="100 900", stretch="100%"),
                face("latin", G + "i.woff2", style="italic"))
    css_2 = sheet(face("latin", G + "n.woff2", family="'Newsreader'"))
    pages = {LINK: css, LINK_2: css_2, G + "e.woff2": A, G + "l.woff2": B, G + "i.woff2": C,
             G + "n.woff2": woff2("n")}
    first = audit(fonts.localize([LINK, LINK_2], fetch=Web(pages)))
    again = audit(fonts.localize((LINK, LINK_2), fetch=Web(dict(reversed(list(pages.items()))))))
    assert first == again and list(first.files) == list(again.files)
    assert first.css.encode("utf-8") == again.css.encode("utf-8")
    assert first.css.isascii()
    # The order is the links' and then the stylesheet's own.
    other = audit(fonts.localize([LINK_2, LINK], fetch=Web(pages)))
    assert other.css.split("\n") == first.css.split("\n")[3:] + first.css.split("\n")[:3]
