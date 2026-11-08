"""``tools/social_cards_export.py`` -- every site card as one tar, for the PC's copy."""
import io
import json
import tarfile

from tools import social_cards_export as x

HOST = "example.test"


def _site(tmp_path):
    blog = {"entries": [
        {"slug": "palantir-stock-thesis", "title": "Palantir a Buy?", "summary": "Revenue doubled.",
         "tags": ["AI", "Software"], "published": "2026-11-04T02:30:00+00:00"},
        {"slug": "no-card-yet", "title": "No card", "summary": "", "tags": [],
         "published": "2026-11-05T15:00:00+00:00"},
        {"slug": "../escape", "title": "Bad", "summary": "", "tags": [],
         "published": "2026-11-05T15:00:00+00:00"},
    ]}
    (tmp_path / "blog.json").write_text(json.dumps(blog), encoding="utf-8")
    card = tmp_path / "blog" / "palantir-stock-thesis"
    card.mkdir(parents=True)
    (card / "card.png").write_bytes(b"PNG")
    (card / "card.jpg").write_bytes(b"JPG")
    (tmp_path / "blog" / "no-card-yet").mkdir()

    ideas = {"days": [{"date": "2026-11-06", "ideas": [
        {"full": "ideas/2026-11-06/1440-META.png", "alt": "Trade idea: META Bear Put Spread"},
        {"full": "ideas/2026-11-06/0940-AVGO.png", "alt": "old card, no JPEG"},
        {"full": "ideas/../../secret.png", "alt": "bad"},
    ]}]}
    (tmp_path / "ideas.json").write_text(json.dumps(ideas), encoding="utf-8")
    day = tmp_path / "ideas" / "2026-11-06"
    day.mkdir(parents=True)
    (day / "1440-META.png").write_bytes(b"MPNG")
    (day / "1440-META.jpg").write_bytes(b"MJPG")
    (day / "0940-AVGO.png").write_bytes(b"APNG")
    return tmp_path


def _tar(items):
    buf = io.BytesIO()
    x.write_tar(items, buf)
    buf.seek(0)
    with tarfile.open(fileobj=buf) as tar:
        return {m.name: tar.extractfile(m).read() for m in tar.getmembers()}


def test_every_card_with_both_files_and_its_caption(tmp_path):
    files = _tar(x.members(_site(tmp_path), host=HOST, max_hashtags=4))
    assert set(files) == {
        "Blog/2026-11-03 palantir-stock-thesis.png", "Blog/2026-11-03 palantir-stock-thesis.jpg",
        "Blog/2026-11-03 palantir-stock-thesis.txt",
        "Trade Ideas/2026-11-06 1440-META.png", "Trade Ideas/2026-11-06 1440-META.jpg",
        "Trade Ideas/2026-11-06 1440-META.txt"}
    assert files["Blog/2026-11-03 palantir-stock-thesis.png"] == b"PNG"
    assert files["Trade Ideas/2026-11-06 1440-META.jpg"] == b"MJPG"
    # The day is the Central-time day of publication (02:30 UTC is the evening before).
    assert files["Blog/2026-11-03 palantir-stock-thesis.txt"].decode("utf-8") == (
        "Palantir a Buy?\n\nRevenue doubled.\n\n"
        "Read it at https://example.test/blog/palantir-stock-thesis/\n\n#AI #Software\n")
    assert files["Trade Ideas/2026-11-06 1440-META.txt"] == b"Trade idea: META Bear Put Spread\n"


def test_no_name_leaves_its_folder(tmp_path):
    names = [n for n, _ in x.members(_site(tmp_path), host=HOST, max_hashtags=4)]
    assert all(n.startswith(("Blog/", "Trade Ideas/")) and ".." not in n and "\\" not in n
               for n in names)


def test_a_missing_or_broken_manifest_is_an_empty_list(tmp_path):
    assert x.members(tmp_path, host=HOST, max_hashtags=4) == []
    (tmp_path / "blog.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "ideas.json").write_text("[]", encoding="utf-8")
    assert x.members(tmp_path, host=HOST, max_hashtags=4) == []


def test_main_refuses_an_empty_site(tmp_path, capsys):
    assert x.main(tmp_path) == 1
    assert "no cards" in capsys.readouterr().err
