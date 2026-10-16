import subprocess
import unittest
from unittest.mock import patch
from scripts import publish


class PublishTests(unittest.TestCase):
    def test_different_push_destination_is_rejected(self):
        answers = iter([str(publish.ROOT), 'https://github.com/ninkynonkpinkyplonk-cell/Silicon-Reflex.git',
                        'git@github.com:Someone/other.git'])
        with patch.object(publish, 'run', side_effect=lambda *a, **kw: next(answers)):
            with self.assertRaisesRegex(RuntimeError, 'push URL'):
                publish.preflight()

    def test_failed_tests_prevent_all_git_mutations(self):
        calls = []
        def operation(*args, **kwargs):
            calls.append(args)
            if 'pytest' in args:
                raise subprocess.CalledProcessError(1, args)
        with patch('sys.argv', ['publish.py']), patch.object(publish, 'preflight'), patch.object(publish, 'run', side_effect=operation):
            with self.assertRaises(subprocess.CalledProcessError):
                publish.main()
        self.assertTrue(calls)
        self.assertFalse(any(args[0] == 'git' for args in calls))

    def test_check_mode_never_stages_commits_or_pushes(self):
        calls = []
        with patch('sys.argv', ['publish.py', '--check']), patch.object(publish, 'run', side_effect=lambda *a, **kw: calls.append(a)):
            publish.main()
        self.assertEqual(len(calls), 2)
        self.assertFalse(any(args[0] == 'git' for args in calls))


if __name__ == '__main__':
    unittest.main()
