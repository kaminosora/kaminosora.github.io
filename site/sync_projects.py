"""Refresh public GitHub projects while retaining the author's curated details.

Run during Pages builds. This never accesses private repositories or Pixiv.
An API/network failure retains the last successful snapshot.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent
OWNER = 'kaminosora'
API = 'https://api.github.com'


def fetch(path):
    headers = {'Accept': 'application/vnd.github+json',
               'User-Agent': 'kaminosora-personal-site',
               'X-GitHub-Api-Version': '2022-11-28'}
    token = os.environ.get('GITHUB_TOKEN')
    if token:
        headers['Authorization'] = 'Bearer ' + token
    request = urllib.request.Request(API + path, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def public_repositories():
    repositories = []
    for page in range(1, 101):
        items = fetch(f'/users/{OWNER}/repos?type=owner&sort=updated&per_page=100&page={page}')
        if not isinstance(items, list):
            raise ValueError('GitHub 返回的仓库列表格式不正确。')
        repositories.extend(item for item in items if not item.get('private') and item.get('owner', {}).get('login', '').lower() == OWNER)
        if len(items) < 100:
            return repositories
    raise ValueError('公开仓库数量超出本次同步上限。')


def merge_project(repo, curated, latest_release=None):
    name = repo['name']
    link = 'https://github.com/' + OWNER + '/' + name
    description = repo.get('description') or '公开仓库，来看看最近又折腾了什么。'
    project = {
        'id': name, 'name': name, 'repo': link, 'tagline': description,
        'summary': description, 'platform': '公开 GitHub 项目',
        'language': repo.get('language') or '未标注语言',
        'license': (repo.get('license') or {}).get('spdx_id'),
        'version': None, 'release_url': None, 'entry_url': link,
        'entry_label': '查看项目说明', 'features': [], 'limits': [],
        'screenshots': [], 'sources': [link],
        'preview_note': '项目资料由 GitHub 公开接口同步；暂无人工挑选的界面截图。',
    }
    project.update(curated or {})
    # These are facts whose current values come from GitHub on every build.
    project.update(id=name, repo=link, language=repo.get('language') or project['language'],
                   stars=repo.get('stargazers_count', 0), forks=repo.get('forks_count', 0),
                   archived=bool(repo.get('archived')), fork=bool(repo.get('fork')),
                   updated_at=repo.get('pushed_at') or repo.get('updated_at'),
                   github_description=repo.get('description'),
                   license=(repo.get('license') or {}).get('spdx_id'),
                   synced_at=datetime.now(timezone.utc).isoformat())
    if project['license'] == 'NOASSERTION':
        project['license'] = None
    if latest_release:
        project.update(version=latest_release['tag_name'], release_url=latest_release['html_url'])
    elif latest_release is False:
        project.update(version=None, release_url=None)
    if not curated:
        project['features'] = ['公开仓库 · ' + str(project['stars']) + ' stars',
                               '最近更新：' + (project['updated_at'] or '')[:10]]
        if latest_release:
            project.update(entry_url=link + '/releases/latest', entry_label='查看最新发布')
        project['limits'] = ['使用方式与兼容性以仓库 README 为准。']
        if project['archived']:
            project['limits'].append('此仓库已归档，不再接收常规更新。')
        if project['fork']:
            project['limits'].append('这是派生仓库，原始项目与许可证以 GitHub 仓库标注为准。')
    return project


def sync(strict=False):
    content = ROOT / 'content'
    current = content / 'projects.json'
    curated_file = content / 'projects-curated.json'
    curated = json.loads((curated_file if curated_file.exists() else current).read_text(encoding='utf-8-sig'))
    curated_map = {p['id']: p for p in curated}
    try:
        repos = public_repositories()
        if not repos:
            raise ValueError('没有返回公开仓库，已保留原有项目快照。')
        result = []
        for repo in repos:
            release = None
            try:
                release = fetch(f'/repos/{OWNER}/{repo["name"]}/releases/latest')
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    release = False
                else:
                    raise
            result.append(merge_project(repo, curated_map.get(repo['name']), release))
        # Preserve the hand-selected order, then show newly discovered repos by activity.
        order = {project['id']: index for index, project in enumerate(curated)}
        result.sort(key=lambda project: order.get(project['id'], len(order)))
    except (OSError, ValueError, KeyError) as exc:
        print('GitHub 同步暂未成功，继续使用已有公开项目快照：' + str(exc), file=sys.stderr)
        if strict:
            raise
        return {'status': 'cached', 'projects': len(json.loads(current.read_text(encoding='utf-8-sig')))}
    if not curated_file.exists():
        curated_file.write_text(json.dumps(curated, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary = current.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(current)
    report = {'status': 'success', 'projects': len(result),
              'syncedAt': datetime.now(timezone.utc).isoformat(),
              'source': f'https://api.github.com/users/{OWNER}/repos',
              'description': '每次发布和每天定时构建时刷新公开仓库；保留人工补充的介绍与截图。'}
    (content / 'projects-sync.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--strict', action='store_true')
    args = parser.parse_args()
    try:
        print(json.dumps(sync(args.strict), ensure_ascii=False))
    except (OSError, ValueError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
