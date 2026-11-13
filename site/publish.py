"""Build a static Pages artifact and publish this site's managed source files.

The local studio is never included in the Pages artifact. GitHub publication
uses the user's existing Git authentication, never a token stored by the app.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
REPOSITORY = 'kaminosora/kaminosora.github.io'
SITE_URL = 'https://kaminosora.github.io/'
ACTIONS_URL = 'https://github.com/' + REPOSITORY + '/actions/workflows/pages.yml'
MAX_FILE = 100 * 1024 * 1024
MAX_SITE = 1_000_000_000
SOURCE_FILES = {
    'build_preview.py', 'library.py', 'studio_core.py', 'studio_server.py',
    'publish.py', 'sync_projects.py', 'launch_studio.py', 'launcher.py',
    'rotation-engine.js', 'sora.css', 'sora.js', 'sora.template.html',
    'standalone.template.html', 'studio.html', 'studio.js', 'studio.css',
    'requirements.txt', 'pages-workflow.yml', 'README.md',
    '启动素材工作台.cmd', '启动素材工作台.ps1',
}
ROOT_FILES = {
    '.gitignore', '.gitattributes', 'README.md', '启动素材工作台.cmd', '启动素材工作台.ps1',
    '.github/workflows/pages.yml',
}
PUBLIC_ROOT_FILES = {'CNAME', 'robots.txt', 'favicon.ico', 'favicon.svg', '404.html'}


def command(args, cwd=None, check=True, timeout=120):
    env = {**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'GCM_INTERACTIVE': 'never',
           'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'}
    completed = subprocess.run(args, cwd=cwd or ROOT, env=env,
                               capture_output=True, text=True, encoding='utf-8',
                               errors='replace', timeout=timeout)
    if check and completed.returncode:
        detail = (completed.stderr or completed.stdout).strip()
        raise ValueError(detail or f'{args[0]} 执行失败。')
    return completed


def git_root():
    try:
        result = command(['git', 'rev-parse', '--show-toplevel'], check=False)
        return Path(result.stdout.strip()).resolve() if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def repository_identity(remote):
    """Accept ordinary HTTPS/SSH origins for the one intended repository."""
    if remote.startswith('git@github.com:'):
        name = remote[len('git@github.com:'):]
    else:
        parsed = urlsplit(remote)
        if parsed.scheme not in {'https', 'ssh'} or parsed.hostname != 'github.com':
            return None
        if parsed.scheme == 'https' and (parsed.username or parsed.password):
            return None
        if parsed.scheme == 'ssh' and parsed.username not in {None, 'git'}:
            return None
        if parsed.port is not None:
            return None
        name = parsed.path.lstrip('/')
    return name.removesuffix('.git').rstrip('/').lower()


def status():
    result = {'available': False, 'branch': '', 'remote': '', 'dirty': False,
              'status': '当前目录还没有连接 Git 仓库，可以保存和预览。',
              'sourceRoot': str(ROOT), 'repoRoot': '',
              'siteUrl': SITE_URL, 'actionsUrl': ACTIONS_URL}
    repo = git_root()
    if not repo:
        return result
    try:
        branch = command(['git', 'branch', '--show-current'], repo).stdout.strip()
        remote = command(['git', 'remote', 'get-url', 'origin'], repo, check=False).stdout.strip()
        changes = command(['git', 'status', '--porcelain'], repo).stdout
        identity = repository_identity(remote)
        valid_layout = ROOT == repo / 'site'
        result.update(branch=branch, remote=('https://github.com/' + identity if identity else '未连接目标仓库'),
                      dirty=bool(changes.strip()), repoRoot=str(repo),
                      available=identity == REPOSITORY and branch == 'main' and valid_layout)
        if identity != REPOSITORY:
            result['status'] = '远端与这个主页的仓库不一致，发布按钮不可用。'
        elif not valid_layout:
            result['status'] = '请从已克隆仓库中的 site 文件夹启动素材工作台。'
        elif branch != 'main':
            result['status'] = '当前不在 main 分支；可以保存预览，发布前请切回 main。'
        else:
            result['status'] = '有本地修改，发布会保存为新的 Git 提交。' if result['dirty'] else '已连接 GitHub，源码没有未提交修改。'
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        result['status'] = str(exc)
    return result


def safe_child(base, relative):
    if not isinstance(relative, str) or not relative or '\\' in relative:
        raise ValueError('构建引用了无效的资源路径。')
    path = (base / relative).resolve()
    if not path.is_relative_to(base.resolve()) or Path(relative).is_absolute():
        raise ValueError('构建资源不能位于项目之外。')
    # Do not silently dereference symlinks, including symlink directories.
    cursor = base
    for part in Path(relative).parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError('发布资源不能使用符号链接：' + relative)
    if not path.is_file():
        raise ValueError('缺少发布资源：' + relative)
    return path


def referenced_media(root=None):
    root = ROOT if root is None else root
    library = json.loads((root / 'content/library.json').read_text(encoding='utf-8-sig'))
    indexes = json.loads((root / 'generated/asset-index.json').read_text(encoding='utf-8-sig'))
    projects = json.loads((root / 'content/projects.json').read_text(encoding='utf-8-sig'))
    ips = {item['id']: item for item in library['ips']}
    characters = {item['id']: item for item in library['characters']}
    assets = {item['id']: item for item in library['assets']}
    paths = set()
    for art in indexes:
        asset = assets.get(art['id'])
        if not asset or not asset.get('enabled', True) or not ips[asset['ipId']].get('enabled', True):
            continue
        if not all(characters[cid].get('enabled', True) for cid in asset.get('characterIds', [])):
            continue
        paths.add(asset['file'])
        for key in ('image', 'background', 'original'):
            if art.get(key):
                paths.add(art[key])
    for project in projects:
        for shot in project.get('screenshots', []):
            for key in ('image', 'full_image'):
                if shot.get(key):
                    paths.add(shot[key])
    for path in paths:
        if not path.startswith('media/'):
            raise ValueError('只允许从 media 目录发布图片：' + path)
        safe_child(root, path)
    return sorted(paths)


def audit_files(folder):
    total = 0
    count = 0
    largest = {'path': '', 'bytes': 0}
    for path in folder.rglob('*'):
        if path.is_symlink():
            raise ValueError('静态产物包含符号链接。')
        if not path.is_file():
            continue
        size = path.stat().st_size
        relative = path.relative_to(folder).as_posix()
        if size >= MAX_FILE:
            raise ValueError(f'图片或文件超过 GitHub 的单文件限制，请替换更小的原图：{relative}')
        total += size
        count += 1
        if size > largest['bytes']:
            largest = {'path': relative, 'bytes': size}
    if total > MAX_SITE:
        raise ValueError('静态网站超过 1 GB，请减少用于发布的素材或替换体积较小的图片。')
    return {'files': count, 'bytes': total, 'largestFile': largest}


def package(build=True):
    if build:
        print('正在生成公开主页并分析图片配色……', flush=True)
        result = command([sys.executable, str(ROOT / 'build_preview.py'), '--production'], timeout=600)
        if result.stdout.strip():
            print(result.stdout.strip(), flush=True)
    safe_child(ROOT, 'index.html')
    paths = referenced_media()
    repo = git_root()
    output = repo / '_site' if repo and ROOT == repo / 'site' else ROOT / 'dist'
    parent = output.parent.resolve()
    if output.resolve().parent != parent or output.name not in {'_site', 'dist'} or output.is_symlink():
        raise ValueError('无法确认构建输出目录，已停止。')
    temporary = Path(tempfile.mkdtemp(prefix='.pages-build-', dir=parent))
    old = None
    try:
        shutil.copy2(ROOT / 'index.html', temporary / 'index.html')
        for relative in paths:
            target = temporary / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(safe_child(ROOT, relative), target)
        (temporary / '.nojekyll').write_text('', encoding='utf-8')
        # Keep only explicit, already-public legacy content, never source folders.
        if repo and ROOT == repo / 'site':
            for name in PUBLIC_ROOT_FILES:
                if (repo / name).is_file():
                    shutil.copy2(safe_child(repo, name), temporary / name)
            legacy = repo / 'lyric-site'
            if legacy.is_dir():
                for source in legacy.rglob('*'):
                    if source.is_symlink():
                        raise ValueError('旧页面目录包含符号链接，无法安全打包。')
                    if source.is_file():
                        relative = source.relative_to(repo).as_posix()
                        if any(part.startswith('.') for part in Path(relative).parts):
                            continue
                        target = temporary / relative
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(safe_child(repo, relative), target)
        report = audit_files(temporary)
        if output.exists():
            old = parent / ('.pages-previous-' + datetime.now().strftime('%Y%m%d%H%M%S%f'))
            output.rename(old)
        temporary.rename(output)
        if old:
            # Both resolved paths were created directly under the known build parent.
            if old.resolve().parent != parent:
                raise ValueError('无法确认旧构建目录位置。')
            shutil.rmtree(old)
        report.update(status='success', output=str(output), siteUrl=SITE_URL,
                      localPreviewOnly=not bool(repo and ROOT == repo / 'site'))
        (ROOT / 'generated/package-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'静态产物已生成：{report["files"]} 个文件，{report["bytes"] / 1024 / 1024:.1f} MiB。', flush=True)
        return report
    except Exception:
        if old and old.exists() and not output.exists():
            old.rename(output)
        raise
    finally:
        if temporary.exists() and temporary.resolve().parent == parent:
            shutil.rmtree(temporary)


def managed(relative):
    """Exact owned roots; uploads, backups, caches and generated output never stage."""
    if relative in ROOT_FILES:
        return True
    if not relative.startswith('site/'):
        return False
    source = relative[5:]
    if source in SOURCE_FILES:
        return True
    if source in {'content/library.json', 'content/projects.json', 'content/projects-curated.json',
                  'content/interests.json', 'content/projects-sync.json'}:
        return True
    if any(part.startswith('.') or part == '__pycache__' for part in Path(source).parts):
        return False
    if source.startswith(('media/originals/', 'media/projects/')):
        return Path(source).suffix.lower() in {'.jpg', '.jpeg', '.png', '.webp', '.avif', '.gif'}
    return source.startswith('tests/') and Path(source).suffix in {'.py', '.cjs', '.js'}


def publish(message=''):
    title = (message or 'Update personal site from material studio').strip()
    if len(title) > 180 or '\n' in title or '\r' in title or not title:
        raise ValueError('提交说明请使用 1–180 个字符的一行文字。')
    current = status()
    if not current['available']:
        raise ValueError(current['status'])
    repo = Path(current['repoRoot'])
    conflicts = command(['git', 'ls-files', '-u'], repo).stdout
    if conflicts:
        raise ValueError('仓库有未解决的合并冲突；内容已保存在本地，请先处理冲突。')
    staged = command(['git', 'diff', '--cached', '--name-only', '-z'], repo).stdout.split('\0')
    if any(path and not managed(path) for path in staged):
        raise ValueError('暂存区包含素材工作台以外的改动，请先处理这些改动后再发布。')
    print('正在检查远端版本……', flush=True)
    command(['git', 'fetch', 'origin', 'main'], repo)
    remote_head = command(['git', 'rev-parse', 'refs/remotes/origin/main'], repo).stdout.strip()
    ancestor = command(['git', 'merge-base', '--is-ancestor', remote_head, 'HEAD'], repo, check=False)
    if ancestor.returncode:
        raise ValueError('GitHub 有本地尚未合并的更新，已保留你的本地改动并停止发布。若没有本地修改，点击“同步 GitHub 更新”；若已有修改，请先用 Git 客户端保存并合并两个版本，再重新发布。')
    package()
    candidates = command(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'], repo).stdout.split('\0')
    paths = sorted({path for path in candidates if path and managed(path)})
    if not paths:
        raise ValueError('没有找到可发布的主页源文件。')
    for relative in paths:
        path = repo / relative
        if path.exists():
            safe_child(repo, relative)
            if path.stat().st_size >= MAX_FILE:
                raise ValueError('素材文件超过 GitHub 单文件限制：' + relative)
    # Chunk argv to remain below Windows command-line length limits.
    for offset in range(0, len(paths), 30):
        command(['git', 'add', '-A', '--', *paths[offset:offset+30]], repo)
    changed = command(['git', 'diff', '--cached', '--quiet'], repo, check=False).returncode
    if changed:
        command(['git', 'commit', '-m', title], repo)
    commit = command(['git', 'rev-parse', 'HEAD'], repo).stdout.strip()
    print('正在推送源码；随后由 GitHub Actions 构建公开页面……', flush=True)
    command(['git', 'push', 'origin', 'HEAD:refs/heads/main'], repo, timeout=600)
    return {'status': 'success', 'commit': commit, 'siteUrl': SITE_URL,
            'actionsUrl': ACTIONS_URL, 'deployment': 'queued',
            'message': '源码已推送。请在 GitHub Actions 查看构建；完成后访问公开主页。'}


def sync_repository():
    """Update a clean checkout by fast-forward only; never stash or merge edits."""
    current = status()
    if not current['available']:
        raise ValueError(current['status'])
    if current['dirty']:
        raise ValueError('本地还有未提交的修改，暂不能同步。请先发布本地版本；若 GitHub 也有新版本，请用 Git 客户端保存并合并两边的修改。工作台不会自动隐藏或覆盖你的改动。')
    repo = Path(current['repoRoot'])
    before = command(['git', 'rev-parse', 'HEAD'], repo).stdout.strip()
    print('正在获取 GitHub 上的最新版本……', flush=True)
    command(['git', 'fetch', 'origin', 'main'], repo)
    # A second check also protects edits made by another program during fetch.
    if command(['git', 'status', '--porcelain'], repo).stdout.strip():
        raise ValueError('获取更新期间出现了新的本地修改，已停止同步并保留这些修改。')
    remote_head = command(['git', 'rev-parse', 'refs/remotes/origin/main'], repo).stdout.strip()
    remote_is_ancestor = command(['git', 'merge-base', '--is-ancestor', remote_head, 'HEAD'], repo, check=False)
    if remote_is_ancestor.returncode == 0:
        return {'status': 'success', 'commit': before, 'restartRequired': False,
                'siteUrl': SITE_URL, 'actionsUrl': ACTIONS_URL,
                'message': '本地已包含 GitHub 上的最新版本。'}
    can_fast_forward = command(['git', 'merge-base', '--is-ancestor', 'HEAD', remote_head], repo, check=False)
    if can_fast_forward.returncode:
        raise ValueError('本地与 GitHub 各有新的提交，需要用 Git 客户端处理合并。已停止同步，没有覆盖任何版本。')
    changed = command(['git', 'diff', '--name-only', '-z', before, remote_head], repo).stdout.split('\0')
    command(['git', 'merge', '--ff-only', remote_head], repo)
    after = command(['git', 'rev-parse', 'HEAD'], repo).stdout.strip()
    restart = any(path.endswith(('.py', '.cmd', '.ps1')) or path.endswith('/requirements.txt') for path in changed)
    message = '已同步 GitHub 更新。'
    message += '工作台程序也有更新，请关闭启动窗口，再双击启动一次。' if restart else '刷新工作台页面即可查看最新配置；生成预览后可查看更新后的主页。'
    return {'status': 'success', 'commit': after, 'restartRequired': restart,
            'siteUrl': SITE_URL, 'actionsUrl': ACTIONS_URL, 'message': message}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='operation', required=True)
    commands.add_parser('status')
    commands.add_parser('package')
    commands.add_parser('sync')
    publishing = commands.add_parser('publish')
    publishing.add_argument('--message', default='')
    args = parser.parse_args()
    try:
        if args.operation == 'status':
            result = status()
        elif args.operation == 'package':
            result = package()
        elif args.operation == 'sync':
            result = sync_repository()
        else:
            result = publish(args.message)
        print(json.dumps(result, ensure_ascii=False), flush=True)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(str(exc), file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
