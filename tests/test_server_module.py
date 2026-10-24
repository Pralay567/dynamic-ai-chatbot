import unittest
from unittest import mock

import server

from helpers import reset_logging


class ServerModuleTests(unittest.TestCase):
    def tearDown(self):
        server._default_app = None
        reset_logging()

    def test_importing_server_has_no_side_effects(self):
        self.assertIsNone(server._default_app)

    def test_app_attribute_is_built_lazily_and_cached(self):
        sentinel = object()
        with mock.patch.object(server, "create_app", return_value=sentinel) as factory:
            self.assertIs(server.app, sentinel)  # what `from server import app` does
            self.assertIs(server.app, sentinel)
        factory.assert_called_once()

    def test_unknown_attribute_still_raises(self):
        with self.assertRaises(AttributeError):
            server.does_not_exist  # noqa: B018

    def test_run_py_imports_cleanly(self):
        import run  # noqa: F401  (must not start a server or open a browser on import)


if __name__ == "__main__":
    unittest.main()
