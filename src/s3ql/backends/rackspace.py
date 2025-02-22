'''
rackspace.py - this file is part of S3QL.

Copyright © 2008 Nikolaus Rath <Nikolaus@rath.org>

This work can be distributed under the terms of the GNU GPLv3.
'''

from ..logging import logging, QuietError  # Ensure use of custom logger class
from . import swiftks
from ..inherit_docstrings import copy_ancestor_docstring
import re

log = logging.getLogger(__name__)


class Backend(swiftks.Backend):
    """A backend to store data in Rackspace CloudFiles"""

    @copy_ancestor_docstring
    def _parse_storage_url(self, storage_url, ssl_context):

        hit = re.match(
            r'^rackspace://'  # Backend
            r'([^/:]+)'  # Region
            r'/([^/]+)'  # Bucketname
            r'(?:/(.*))?$',  # Prefix
            storage_url,
        )
        if not hit:
            raise QuietError('Invalid storage URL', exitcode=2)

        region = hit.group(1)
        containername = hit.group(2)
        prefix = hit.group(3) or ''

        if ssl_context:
            port = 443
        else:
            port = 80

        self.hostname = 'auth.api.rackspacecloud.com'
        self.port = port
        self.container_name = containername
        self.prefix = prefix
        self.region = region
