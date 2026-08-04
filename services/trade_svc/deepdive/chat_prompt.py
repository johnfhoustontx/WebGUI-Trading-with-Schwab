"""
MakeChatPrompt - build a drop-in chat prompt from EquityDeepDive output
Version: 1.0.0
Last Updated: 2026-08-03

Injects the quantitative digest from an equity_deep_dive.py JSON dump into
chat_query_template.md and writes a finished markdown file that can be dragged
straight into a chat window. No API calls, no keys.

Version 1.0.0 Changes:
- Initial implementation
"""
import re
import sys
import json
import logging
import argparse
import datetime as dt
from pathlib import Path

#############################################
# LOGGING SETUP
#############################################

# NOTE: no logging.basicConfig here — the trade_svc scaffold owns root-logger setup.
logger = logging.getLogger(__name__)

#############################################
# CONSTANTS
#############################################

TEMPLATE_NAME = 'chat_query_template.md'
DEFAULT_OUTPUT_DIR = Path('./reports')

# Strips the HOW TO USE block so it doesn't end up in the model's context
COMMENT_BLOCK = re.compile(r'<!--.*?-->\s*', re.DOTALL)


#############################################
# DIGEST
#############################################

def get_digest(data):
    """Build the quantitative digest via the local (pure, no-API) formatter.

    ``ai_analyst.py`` (the Anthropic path) was deliberately NOT migrated; its pure
    ``build_quant_digest`` formatter lives in ``digest.py`` here instead.
    """
    from . import digest
    return digest.build_quant_digest(data)


#############################################
# BUILD
#############################################

def build_prompt(data, template_text, keep_comment=False):
    """Fill the template placeholders

    Args:
        data: parsed equity_deep_dive JSON
        template_text: raw template contents
        keep_comment: leave the HOW TO USE block in place

    Returns:
        Finished prompt text
    """
    symbol = str(data.get('symbol', '?')).lstrip('$')

    text = template_text
    if not keep_comment:
        text = COMMENT_BLOCK.sub('', text, count=1).lstrip()

    replacements = {
        '{{SYMBOL}}': symbol,
        '{{TODAY}}': dt.date.today().strftime('%Y-%m-%d (%A)'),
        '{{QUANT_DATA}}': get_digest(data),
    }
    for token, value in replacements.items():
        text = text.replace(token, value)

    leftover = re.findall(r'\{\{[A-Z_]+\}\}', text)
    if leftover:
        logger.warning(f'Unfilled placeholders remain: {sorted(set(leftover))}')

    return text


def find_template(explicit=None):
    """Locate the template next to this script unless told otherwise"""
    if explicit:
        path = Path(explicit)
        if not path.exists():
            raise FileNotFoundError(f'Template not found: {path}')
        return path

    local = Path(__file__).resolve().parent / TEMPLATE_NAME
    if local.exists():
        return local

    cwd = Path.cwd() / TEMPLATE_NAME
    if cwd.exists():
        return cwd

    raise FileNotFoundError(
        f'{TEMPLATE_NAME} not found beside {Path(__file__).name} or in the '
        f'working directory. Pass --template to point at it.'
    )


def latest_dump(output_dir, symbol=None):
    """Most recent deep-dive JSON in the reports directory"""
    output_dir = Path(output_dir)
    if not output_dir.exists():
        return None
    pattern = f'{symbol.upper()}_deepdive_*.json' if symbol else '*_deepdive_*.json'
    matches = sorted(output_dir.glob(pattern))
    return matches[-1] if matches else None


#############################################
# CLI
#############################################

def parse_args():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(
        description='Build a drop-in chat prompt from an EquityDeepDive JSON dump',
        epilog='Examples:\n'
               '  python make_chat_prompt.py reports\\OKLO_deepdive_20260803_143000.json\n'
               '  python make_chat_prompt.py --latest OKLO\n'
               '  python make_chat_prompt.py --latest OKLO --clipboard\n'
               '  python make_chat_prompt.py --latest --stdout | clip',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('json_path', nargs='?', default=None,
                        help='Path to an equity_deep_dive.py --json dump')
    parser.add_argument('--latest', nargs='?', const='', default=None,
                        metavar='SYMBOL',
                        help='Use the newest dump in the reports dir, optionally '
                             'for a given symbol')
    parser.add_argument('--template', default=None,
                        help=f'Path to {TEMPLATE_NAME}')
    parser.add_argument('-o', '--output-dir', default=str(DEFAULT_OUTPUT_DIR),
                        help='Where to look for dumps and write the prompt')
    parser.add_argument('--stdout', action='store_true',
                        help='Print to stdout instead of writing a file')
    parser.add_argument('--clipboard', action='store_true',
                        help='Also copy the prompt to the clipboard')
    parser.add_argument('--keep-comment', action='store_true',
                        help='Keep the HOW TO USE block in the output')
    return parser.parse_args()


#############################################
# MAIN
#############################################

def main():
    args = parse_args()

    # ---- Resolve the input dump
    if args.json_path:
        json_path = Path(args.json_path)
    elif args.latest is not None:
        json_path = latest_dump(args.output_dir, args.latest or None)
        if json_path is None:
            logger.error(f'No deep-dive dumps found in {args.output_dir}. '
                         f'Run equity_deep_dive.py with --json first.')
            sys.exit(1)
        logger.info(f'Using latest dump: {json_path.name}')
    else:
        logger.error('Provide a JSON path or use --latest.')
        sys.exit(1)

    if not json_path.exists():
        logger.error(f'Not found: {json_path}')
        sys.exit(1)

    try:
        data = json.loads(json_path.read_text(encoding='utf-8'))
    except json.JSONDecodeError as exc:
        logger.error(f'Could not parse {json_path}: {exc}')
        sys.exit(1)

    # ---- Load the template
    try:
        template_path = find_template(args.template)
    except FileNotFoundError as exc:
        logger.error(str(exc))
        sys.exit(1)

    template_text = template_path.read_text(encoding='utf-8')
    prompt = build_prompt(data, template_text, keep_comment=args.keep_comment)

    # ---- Emit
    if args.stdout:
        print(prompt)
        return

    symbol = str(data.get('symbol', 'UNKNOWN')).lstrip('$')
    stamp = dt.datetime.now().strftime('%Y%m%d_%H%M%S')
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f'{symbol}_chatquery_{stamp}.md'
    out_path.write_text(prompt, encoding='utf-8')

    if args.clipboard:
        try:
            import subprocess
            subprocess.run('clip', input=prompt.encode('utf-16-le'), check=True)
            logger.info('Copied to clipboard')
        except Exception as exc:
            logger.warning(f'Clipboard copy failed: {exc}')

    logger.info(f'Prompt written: {out_path.resolve()}')
    logger.info(f'{len(prompt):,} characters - drag this file into a chat window.')
