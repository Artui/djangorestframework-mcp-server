"""Conformance-suite-local conftest.

The root ``tests/conftest.py`` already provides the ``jsonrpc`` /
``initialized_session`` / ``client`` fixtures we re-use. The conformance
suite's only added contract is that every test routes against
``tests.conformance.urls`` — applied via a ``pytestmark`` declaration
at the top of each test module so pytest-django's ``urls`` marker is
scoped to this package only. (A blanket
``pytest_collection_modifyitems`` would override the URL conf for every
test in the suite, including the non-conformance ones.)
"""

from __future__ import annotations
