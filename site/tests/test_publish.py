import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import publish
import sync_projects


def write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(content) if isinstance(content, (list, dict)) else content, encoding='utf-8')


class PublishTests(unittest.TestCase):
    def fixture(self, root):
        write(root / 'index.html', '<!doctype html><h1>Public site</h1>')
        write(root / 'studio.html', 'private local management UI')
        write(root / '.studio/token', 'local secret')
        write(root / 'content/backups/library.json', {'private': True})
        write(root / 'content/library.json', {
            'ips': [{'id': 'ip'}], 'characters': [],
            'assets': [{'id': 'a', 'ipId': 'ip', 'file': 'media/originals/原神/甘雨.png'},
                       {'id': 'off', 'ipId': 'ip', 'file': 'media/originals/hidden.png', 'enabled': False}]})
        write(root / 'content/projects.json', [])
        write(root / 'generated/asset-index.json', [
            {'id': 'a', 'image': 'media/derived/a.thumb.webp', 'background': 'media/derived/a.display.webp'},
            {'id': 'off', 'image': 'media/derived/off.thumb.webp'}])
        for relative in ['media/originals/原神/甘雨.png', 'media/derived/a.thumb.webp', 'media/derived/a.display.webp',
                         'media/originals/hidden.png', 'media/derived/off.thumb.webp', 'media/derived/stale.webp']:
            write(root / relative, 'image fixture')

    def test_static_artifact_only_contains_referenced_public_files_and_replaces_stale_build(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            write(root / 'dist/stale-file.txt', 'old build')
            with patch.object(publish, 'ROOT', root), patch.object(publish, 'git_root', return_value=None):
                report = publish.package(build=False)
            files = {p.relative_to(root / 'dist').as_posix() for p in (root / 'dist').rglob('*') if p.is_file()}
            self.assertEqual(files, {'index.html', '.nojekyll', 'media/originals/原神/甘雨.png',
                                     'media/derived/a.thumb.webp', 'media/derived/a.display.webp'})
            self.assertEqual(report['files'], len(files))
            self.assertTrue((root / '.studio/token').exists())

    def test_missing_or_traversing_asset_keeps_previous_successful_build(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            write(root / 'dist/index.html', 'previous successful build')
            (root / 'media/originals/原神/甘雨.png').unlink()
            with patch.object(publish, 'ROOT', root), patch.object(publish, 'git_root', return_value=None):
                with self.assertRaises(ValueError):
                    publish.package(build=False)
                with self.assertRaises(ValueError):
                    publish.safe_child(root, '../outside.txt')
            self.assertEqual((root / 'dist/index.html').read_text(), 'previous successful build')

    def test_publication_scope_excludes_private_runtime_and_unrelated_changes(self):
        for path in ['site/.studio/uploads/a.png', 'site/content/backups/x.json',
                     'site/generated/manager-data.js', 'site/media/derived/a.webp',
                     '.env', 'site/.env', 'site/secrets.py', 'lyric-site/index.html']:
            with self.subTest(path=path):
                self.assertFalse(publish.managed(path))
        for path in ['site/studio_core.py', 'site/content/library.json',
                     'site/media/originals/原神/甘雨.png', '.github/workflows/pages.yml', 'README.md']:
            self.assertTrue(publish.managed(path))

    def test_remote_identity_rejects_embedded_credentials_and_lookalike_hosts(self):
        for remote in ['https://token@github.com/kaminosora/kaminosora.github.io.git',
                       'https://github.com.evil.test/kaminosora/kaminosora.github.io.git',
                       'https://github.com:444/kaminosora/kaminosora.github.io.git',
                       'file:///tmp/repo.git']:
            self.assertIsNone(publish.repository_identity(remote))
        for remote in ['git@github.com:kaminosora/kaminosora.github.io.git',
                       'https://github.com/kaminosora/kaminosora.github.io.git',
                       'ssh://git@github.com/kaminosora/kaminosora.github.io.git']:
            self.assertEqual(publish.repository_identity(remote), publish.REPOSITORY)

    def test_curated_project_details_survive_sync_while_remote_facts_refresh(self):
        repo = {'name': 'example', 'description': 'Latest public description', 'language': 'Python',
                'stargazers_count': 7, 'license': {'spdx_id': 'MIT'}, 'pushed_at': '2026-11-14T00:00:00Z'}
        curated = {'id': 'example', 'name': '定制项目名', 'features': ['手写介绍'],
                   'screenshots': [{'image': 'media/projects/shot.png'}], 'version': 'v1'}
        merged = sync_projects.merge_project(repo, curated, {'tag_name': 'v2', 'html_url': 'https://github.com/kaminosora/example/releases/tag/v2'})
        self.assertEqual(merged['name'], '定制项目名')
        self.assertEqual(merged['features'], ['手写介绍'])
        self.assertEqual(merged['screenshots'], curated['screenshots'])
        self.assertEqual(merged['stars'], 7)
        self.assertEqual(merged['version'], 'v2')

    def test_failed_sync_preserves_last_successful_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = [{'id': 'kept', 'name': 'Kept'}]
            write(root / 'content/projects.json', original)
            with patch.object(sync_projects, 'ROOT', root), patch.object(sync_projects, 'public_repositories', side_effect=OSError('offline')):
                self.assertEqual(sync_projects.sync()['status'], 'cached')
            self.assertEqual(json.loads((root / 'content/projects.json').read_text()), original)


class RepositorySyncTests(unittest.TestCase):
    """Exercise real local Git histories without connecting to a network remote."""
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.env = patch.dict(os.environ, {'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull})
        self.env.start()
        self.remote = self.root / 'remote.git'
        self.author = self.root / 'author'
        self.client = self.root / 'client'
        self.git(self.root, 'init', '--bare', str(self.remote))
        self.git(self.root, 'init', '-b', 'main', str(self.author))
        self.configure(self.author)
        write(self.author / 'site/studio_core.py', '# first version\n')
        self.git(self.author, 'add', '.')
        self.git(self.author, 'commit', '-m', 'Initial version')
        self.git(self.author, 'remote', 'add', 'origin', str(self.remote))
        self.git(self.author, 'push', '-u', 'origin', 'main')
        self.git(self.root, 'clone', '--branch', 'main', str(self.remote), str(self.client))
        self.configure(self.client)
        self.source_patch = patch.object(publish, 'ROOT', self.client / 'site')
        self.status_patch = patch.object(publish, 'status', return_value={
            'available': True, 'dirty': False, 'repoRoot': str(self.client)})
        self.source_patch.start()
        self.status_patch.start()

    def tearDown(self):
        self.status_patch.stop()
        self.source_patch.stop()
        self.env.stop()
        self.directory.cleanup()

    def git(self, cwd, *args):
        completed = subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True,
                                   encoding='utf-8', errors='replace', check=True)
        return completed.stdout.strip()

    def configure(self, repo):
        self.git(repo, 'config', 'user.name', 'Local test')
        self.git(repo, 'config', 'user.email', 'local-test@example.invalid')

    def remote_update(self):
        write(self.author / 'site/studio_core.py', '# updated application\n')
        self.git(self.author, 'add', '.')
        self.git(self.author, 'commit', '-m', 'Remote update')
        self.git(self.author, 'push', 'origin', 'main')

    def test_clean_checkout_fast_forwards_and_flags_python_restart(self):
        self.remote_update()
        expected = self.git(self.author, 'rev-parse', 'HEAD')
        result = publish.sync_repository()
        self.assertEqual(result['commit'], expected)
        self.assertTrue(result['restartRequired'])
        self.assertEqual((self.client / 'site/studio_core.py').read_text(), '# updated application\n')
        again = publish.sync_repository()
        self.assertFalse(again['restartRequired'])

    def test_diverged_histories_leave_local_version_untouched(self):
        self.remote_update()
        write(self.client / 'site/studio_core.py', '# local application edit\n')
        self.git(self.client, 'add', '.')
        self.git(self.client, 'commit', '-m', 'Local update')
        before = self.git(self.client, 'rev-parse', 'HEAD')
        with self.assertRaisesRegex(ValueError, '各有新的提交'):
            publish.sync_repository()
        self.assertEqual(self.git(self.client, 'rev-parse', 'HEAD'), before)
        self.assertEqual((self.client / 'site/studio_core.py').read_text(), '# local application edit\n')

    def test_uncommitted_changes_stop_before_fetch(self):
        with patch.object(publish, 'status', return_value={'available': True, 'dirty': True}), patch.object(publish, 'command') as commands:
            with self.assertRaisesRegex(ValueError, '未提交'):
                publish.sync_repository()
            commands.assert_not_called()


if __name__ == '__main__':
    unittest.main()
