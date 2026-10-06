"""Clean ONE document in a throwaway process. Run as a module:

    python -m services.blog_svc.clean_worker

It reads the document as UTF-8 bytes from stdin to EOF, calls ``clean.clean``,
writes ONE JSON object to stdout (``html``, ``title``, ``summary``, ``removed``,
``font_links``, ``reason``) and exits 0. Nothing else ever goes to stdout - the
parent parses stdout as that one object - so logging stays on stderr, which the
parent captures and bounds.

Why a separate process at all: the HTML parser's cost is not linear in the
input (one tag with tens of thousands of attributes parses in minutes at the
size limit), and three rounds of review showed a pre-parse scan cannot predict
that cost safely. ``clean_bound.clean_bounded`` runs this worker and kills it at
a wall-clock limit, so a slow or memory-hungry document costs a killed process,
not a stuck service thread. See the design doc, "The cleaner runs in a worker
process with a time limit".

On POSIX the worker first lowers its OWN limits with ``resource.setrlimit`` -
address space to ``BLOG_CLEAN_MEM_MB`` and CPU seconds to ``BLOG_CLEAN_CPU_SEC``,
both passed in the environment by the parent - so a memory blow-up or a
runaway loop inside the parser is contained even before the wall-clock kill.
On Windows ``resource`` does not exist and this is skipped: there the parent's
wall-clock kill is the whole bound.

Imports are kept to the few this needs (``clean`` pulls in lxml and tinycss2,
which is the unavoidable cost; everything else is stdlib) so the per-document
process start stays small.
"""
import json
import os
import sys


def _apply_limits() -> None:
    """Lower this process's address space and CPU time. POSIX only; a no-op
    where ``resource`` is absent (Windows) or a limit cannot be set."""
    try:
        import resource
    except ImportError:
        return
    megabytes = _env_int("BLOG_CLEAN_MEM_MB", 512)
    cpu_seconds = _env_int("BLOG_CLEAN_CPU_SEC", 30)
    for which, value in ((resource.RLIMIT_AS, megabytes * 1024 * 1024),
                         (resource.RLIMIT_CPU, cpu_seconds)):
        try:
            _soft, hard = resource.getrlimit(which)
            ceiling = value if hard == resource.RLIM_INFINITY else min(value, hard)
            resource.setrlimit(which, (ceiling, hard))
        except (ValueError, OSError):       # a limit the OS will not take: leave it
            pass


def _env_int(name, default) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def main() -> int:
    _apply_limits()
    from services.blog_svc import clean
    html = sys.stdin.buffer.read().decode("utf-8", "replace")
    result = clean.clean(html)
    payload = {"html": result.html, "title": result.title, "summary": result.summary,
               "removed": result.removed, "font_links": list(result.font_links),
               "reason": result.reason}
    sys.stdout.write(json.dumps(payload))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
