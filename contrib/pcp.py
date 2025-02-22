#!/usr/bin/env python3
'''
pcp.py - this file is part of S3QL.

Parallel, recursive copy of directory trees.

---
Copyright © 2008 Nikolaus Rath <Nikolaus@rath.org>

This work can be distributed under the terms of the GNU GPLv3.
'''

import sys
import os
import subprocess
from argparse import ArgumentTypeError

# We are running from the S3QL source directory, make sure
# that we use modules from this directory
basedir = os.path.abspath(os.path.join(os.path.dirname(sys.argv[0]), '..'))
if os.path.exists(os.path.join(basedir, 'setup.py')) and os.path.exists(
    os.path.join(basedir, 'src', 's3ql', '__init__.py')
):
    sys.path = [os.path.join(basedir, 'src')] + sys.path

from s3ql.logging import logging, setup_logging, setup_warnings
from s3ql.parse_args import ArgumentParser

log = logging.getLogger(__name__)

pool = ('abcdefghijklmnopqrstuvwxyz', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', '0123456789')


def processes_to_int(string):
    value = int(string)
    if value < 2 or value > len(pool[0]) + 1:
        raise ArgumentTypeError(
            "%d needs to be between 2 and %d (inclusive)" % (value, len(pool[0]) + 1)
        )
    return value


def parse_args(args):
    '''Parse command line'''

    parser = ArgumentParser(
        description='Recursively copy source(s) to destination using multiple '
        'parallel rsync processes.'
    )

    parser.add_log()
    parser.add_quiet()
    parser.add_debug()
    parser.add_version()

    parser.add_argument("-a", action="store_true", help='Pass -aHAX option to rsync.')
    parser.add_argument(
        "--processes",
        action="store",
        type=processes_to_int,
        metavar='<no>',
        default=10,
        help='Number of rsync processes to use (default: %(default)s).',
    )

    parser.add_argument('source', metavar='<source>', nargs='+', help='Directories to copy')
    parser.add_argument('dest', metavar='<destination>', help="Target directory")

    options = parser.parse_args(args)
    options.pps = options.source + [options.dest]

    return options


def main(args=None):
    if args is None:
        args = sys.argv[1:]

    setup_warnings()
    options = parse_args(args)
    setup_logging(options)

    steps = [len(x) / (options.processes - 1) for x in pool]
    prefixes = list()
    for i in range(options.processes - 1):
        parts = [x[int(i * y) : int((i + 1) * y)] for (x, y) in zip(pool, steps)]
        prefixes.append(''.join(parts))

    filters = ['-! [%s]*' % x for x in prefixes]

    # Catch all
    filters.append('- [%s]*' % ''.join(prefixes))

    rsync_args = ['rsync', '-f', '+ */']
    if not options.quiet:
        rsync_args.append('--out-format')
        rsync_args.append('%n%L')
    if options.a:
        rsync_args.append('-aHAX')

    processes = list()
    for filter_ in filters:
        cmd = rsync_args + ['-f', filter_] + options.pps
        log.debug('Calling %s', cmd)
        processes.append(subprocess.Popen(cmd))

    if all([c.wait() == 0 for c in processes]):
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == '__main__':
    main(sys.argv[1:])
