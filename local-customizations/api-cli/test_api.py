import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location('api_cli', Path(__file__).with_name('codex-api-chat.py'))
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


class ApiTests(unittest.TestCase):
    def test_overrides_preserve_values_and_stop_at_subcommand(self):
        args = ['-c', 'model="with spaces"', '--config=features.steer_only=true',
                'exec', '-c', 'model="child"', 'literal $(value)']
        prefix, rest = api.split_config_prefix(args)
        self.assertEqual(prefix, args[:3])
        self.assertEqual(rest, args[3:])
        with patch.object(api, 'codex_bin', return_value='/fixture/codex'):
            command = api.command(rest, prefix)
        self.assertEqual(command[3:], args)

    def test_missing_config_value_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'requires a configuration value'):
            api.split_config_prefix(['-c'])

    def test_login_with_leading_overrides_reaches_login_without_existing_auth(self):
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(api, 'API_HOME', Path(temporary)), \
                patch.object(api, 'codex_bin', return_value='/fixture/codex'), \
                patch.object(sys, 'argv', ['codex-api-chat', '-c', 'model="marker"',
                                         'login', '--with-api-key']), \
                patch.object(api.subprocess, 'call', return_value=0) as call:
            self.assertEqual(api.main(), 0)
        self.assertEqual(call.call_args.args[0], [
            '/fixture/codex', '-c', 'cli_auth_credentials_store="file"',
            '-c', 'model="marker"', 'login', '--with-api-key',
        ])

    def test_explicit_model_override_wins_over_environment_default(self):
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(api, 'API_HOME', Path(temporary)), \
                patch.dict(os.environ, {'CODEX_API_MODEL': 'environment-model'}), \
                patch.object(api, 'codex_bin', return_value='/fixture/codex'), \
                patch.object(api.os, 'execvpe') as execute:
            (Path(temporary) / 'auth.json').write_text('{}')
            api.run_codex([], ['-c', 'model="explicit"'])
        self.assertEqual(execute.call_args.args[1][-2:], ['-c', 'model="explicit"'])


if __name__ == '__main__':
    unittest.main()
