"""The conftest redirects x_post's jsonl for every test in this package, so a
test that forgets its own patch cannot append to the real log."""
import repo_paths
from shared.notify import x_post


def test_conftest_keeps_the_jsonl_out_of_the_checkout():
    assert x_post.X_POSTS_LOG != repo_paths.X_POSTS_LOG
