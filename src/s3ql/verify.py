'''
verify.py - this file is part of S3QL.

Copyright © 2014 Nikolaus Rath <Nikolaus@rath.org>

This work can be distributed under the terms of the GNU GPLv3.
'''

from .logging import logging, setup_logging, setup_warnings
from .mount import get_metadata
from . import BUFSIZE
from .common import get_backend_factory, pretty_print_size, AsyncFn
from .backends.common import NoSuchObject, CorruptedObjectError
from .parse_args import ArgumentParser
from queue import Queue, Full as QueueFull
import os
import argparse
import time
import signal
import faulthandler
import sys
import textwrap
import atexit

log = logging.getLogger(__name__)


def _new_file_type(s, encoding='utf-8'):
    '''An argparse type for a file that does not yet exist'''

    if os.path.exists(s) and os.stat(s).st_size != 0:
        msg = 'File already exists - refusing to overwrite: %s' % s
        raise argparse.ArgumentTypeError(msg)

    fh = open(s, 'w', encoding=encoding)
    atexit.register(fh.close)
    return fh


def parse_args(args):
    '''Parse command line'''

    parser = ArgumentParser(
        description=textwrap.dedent(
            '''\
        Verifies that all data in an S3QL file system can be downloaded
        from the storage backend.

        In contrast to fsck.s3ql, this program does not trust the object
        listing returned by the backend, but actually attempts to retrieve
        every object. It therefore takes a lot longer.
        '''
        )
    )

    parser.add_log()
    parser.add_debug()
    parser.add_quiet()
    parser.add_version()
    parser.add_cachedir()
    parser.add_backend_options()
    parser.add_storage_url()

    parser.add_argument(
        "--missing-file",
        type=_new_file_type,
        metavar='<name>',
        default='missing_objects.txt',
        help="File to store keys of missing objects.",
    )

    parser.add_argument(
        "--corrupted-file",
        type=_new_file_type,
        metavar='<name>',
        default='corrupted_objects.txt',
        help="File to store keys of corrupted objects.",
    )

    parser.add_argument(
        "--data",
        action="store_true",
        default=False,
        help="Read every object completely, instead of checking just the metadata.",
    )

    parser.add_argument(
        "--parallel", default=4, type=int, help="Number of connections to use in parallel."
    )

    parser.add_argument(
        "--start-with",
        default=0,
        type=int,
        metavar='<n>',
        help="Skip over first <n> objects and with verifying object <n>+1.",
    )

    options = parser.parse_args(args)

    return options


def main(args=None):
    faulthandler.enable()
    faulthandler.register(signal.SIGUSR1)

    if args is None:
        args = sys.argv[1:]

    setup_warnings()
    options = parse_args(args)
    setup_logging(options)

    backend_factory = get_backend_factory(options)

    # Retrieve metadata
    with backend_factory() as backend:
        (param, db) = get_metadata(backend, options.cachepath)

    retrieve_objects(
        db,
        backend_factory,
        options.corrupted_file,
        options.missing_file,
        thread_count=options.parallel,
        full=options.data,
        offset=options.start_with,
    )

    if options.corrupted_file.tell() or options.missing_file.tell():
        sys.exit(46)
    else:
        os.unlink(options.corrupted_file.name)
        os.unlink(options.missing_file.name)
        sys.exit(0)


def retrieve_objects(
    db, backend_factory, corrupted_fh, missing_fh, thread_count=1, full=False, offset=0
):
    """Attempt to retrieve every object"""

    log.info('Reading all objects...')

    queue = Queue(thread_count)
    threads = []
    for _ in range(thread_count):
        t = AsyncFn(_retrieve_loop, queue, backend_factory, corrupted_fh, missing_fh, full)
        # Don't wait for worker threads, gives deadlock if main thread
        # terminates with exception
        t.daemon = True
        t.start()
        threads.append(t)

    total_size = db.get_val('SELECT SUM(phys_size) FROM objects WHERE phys_size > 0')
    total_count = db.get_val('SELECT COUNT(id) FROM objects')
    size_acc = 0

    sql = 'SELECT id, phys_size, length FROM objects ORDER BY id'
    i = 0  # Make sure this is set if there are zero objects
    stamp1 = 0
    try:
        for (i, (obj_id, obj_size, block_size)) in enumerate(db.query(sql)):
            i += 1  # start at 1
            stamp2 = time.time()
            if stamp2 - stamp1 > 1 or i == total_count:
                stamp1 = stamp2
                progress = '%d objects (%.2f%%)' % (i, i / total_count * 100)
                if full:
                    s = pretty_print_size(size_acc)
                    progress += ' / %s (%.2f%%)' % (s, size_acc / total_size * 100)
                sys.stdout.write('\r..processed %s so far..' % progress)
                sys.stdout.flush()

                # Terminate early if any thread failed with an exception
                for t in threads:
                    if not t.is_alive():
                        t.join_and_raise()

            size_acc += obj_size
            if i < offset:
                continue

            # Avoid blocking if all threads terminated
            while True:
                try:
                    queue.put((obj_id, block_size), timeout=1)
                except QueueFull:
                    pass
                else:
                    break
                for t in threads:
                    if not t.is_alive():
                        t.join_and_raise()

    finally:
        sys.stdout.write('\n')

    queue.maxsize += len(threads)
    for t in threads:
        queue.put(None)

    for t in threads:
        t.join_and_raise()

    log.info('Verified all %d storage objects.', i)


def _retrieve_loop(queue, backend_factory, corrupted_fh, missing_fh, full=False):
    '''Retrieve object ids arriving in *queue* from *backend*

    If *full* is False, lookup and read metadata. If *full* is True,
    read entire object.

    Corrupted objects are written into *corrupted_fh*. Missing objects
    are written into *missing_fh*.

    Terminate when None is received.
    '''

    with backend_factory() as backend:
        size = None

        def do_read(fh):
            nonlocal size
            size = 0
            while True:
                buf = fh.read(BUFSIZE)
                size += len(buf)
                if not buf:
                    break

        while True:
            el = queue.get()
            if el is None:
                break
            (obj_id, exp_size) = el

            log.debug('reading object %s', obj_id)
            key = 's3ql_data_%d' % obj_id
            try:
                if full:
                    backend.perform_read(do_read, key)
                else:
                    backend.lookup(key)
            except NoSuchObject:
                log.warning('Backend seems to have lost object %d', obj_id)
                print(key, file=missing_fh)
                continue
            except CorruptedObjectError:
                log.warning('Object %d is corrupted', obj_id)
                print(key, file=corrupted_fh)
                continue

            if full and exp_size != size:
                log.warning(
                    'Object %d is corrupted (expected size %d, actual size %d)',
                    obj_id,
                    exp_size,
                    size,
                )
                print(key, file=corrupted_fh)
                continue


if __name__ == '__main__':
    main(sys.argv[1:])
