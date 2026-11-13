"""Double-click bootstrap: one private Python environment, then the local UI."""
from pathlib import Path
import os
import subprocess
import sys
import venv


def main():
    root=Path(__file__).resolve().parent
    repo=root.parent if root.name=='site' else root
    envdir=repo/'.venv'
    python=envdir/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    print('空想便笺 · 正在准备素材工作台…',flush=True)
    if sys.version_info<(3,10):
        raise RuntimeError('请安装 Python 3.10 或更新版本，再双击启动。')
    if not python.exists():
        print('首次启动：正在创建项目专用环境，不会修改系统 Python。',flush=True)
        venv.create(envdir,with_pip=True)
    env={**os.environ,'PYTHONUTF8':'1','PYTHONIOENCODING':'utf-8'}
    check=subprocess.run([str(python),'-c','import PIL; assert PIL.__version__ == "11.2.1"'],env=env,capture_output=True)
    if check.returncode:
        print('首次准备图片工具，需要联网下载。之后启动无需重复安装。',flush=True)
        subprocess.run([str(python),'-m','pip','install','-r',str(root/'requirements.txt')],check=True,env=env)
    subprocess.run([str(python),'-X','utf8',str(root/'studio_core.py')],check=True,env=env,cwd=root,stdout=subprocess.DEVNULL)
    print('正在更新本地预览…',flush=True)
    build_code='import studio_core, build_preview\nwith studio_core.LOCK:\n    build_preview.build()\n'
    subprocess.run([str(python),'-X','utf8','-c',build_code],check=True,env=env,cwd=root,stdout=subprocess.DEVNULL)
    print('浏览器即将打开。保留这个窗口，关闭窗口即可结束工作台。',flush=True)
    subprocess.run([str(python),'-X','utf8',str(root/'studio_server.py')],check=True,env=env,cwd=root)


if __name__=='__main__':
    try:
        main()
    except (OSError,RuntimeError,subprocess.CalledProcessError) as exc:
        print('\n启动未完成：'+str(exc))
        print('请检查网络和 Python 安装。已保存的素材不会丢失。')
        input('按回车关闭…')
        sys.exit(1)
