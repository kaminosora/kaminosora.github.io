"""Loopback-only material studio. The published site never needs this server."""
from __future__ import annotations

import argparse
import copy
import hmac
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib
import ipaddress
import json
import mimetypes
from pathlib import Path
import secrets
import socket
import ssl
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, quote, unquote, urljoin, urlsplit
import webbrowser


ROOT = Path(__file__).resolve().parent
MAX_IMAGE_BYTES = 80 * 1024 * 1024
MAX_METADATA_BYTES = 8 * 1024 * 1024
MAX_JSON_BYTES = 1024 * 1024
MAX_URLS = 25
MAX_BATCH_BYTES = 512 * 1024 * 1024
NETWORK_TIMEOUT = 20
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
LOCAL_IMPORT_HINT = "请在浏览器中正常下载原图，再使用“选择本地图片”导入。"


class RequestError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _is_public_address(value):
    address = ipaddress.ip_address(value)
    if not address.is_global or address.is_multicast or address.is_unspecified or address.is_reserved:
        return False
    if isinstance(address, ipaddress.IPv6Address):
        for embedded in (address.ipv4_mapped, address.sixtofour):
            if embedded and not _is_public_address(str(embedded)):
                return False
        if address.teredo and any(not _is_public_address(str(part)) for part in address.teredo):
            return False
    return True


def public_target(url, resolver=socket.getaddrinfo):
    """Resolve once, reject every non-public answer, then connect to that exact IP."""
    if not isinstance(url, str) or len(url) > 8192 or any(ord(c) < 32 for c in url) or "\\" in url:
        raise RequestError("图片地址格式不正确。")
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError()
        if parsed.username is not None or parsed.password is not None:
            raise RequestError("图片地址不能包含账号、密码或登录信息。")
        hostname = parsed.hostname.encode("idna").decode("ascii").rstrip(".")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if port not in {80, 443} or "%" in hostname or not hostname:
            raise ValueError()
        # Reject alternative numeric address forms rather than let the resolver
        # interpret them as a private machine (e.g. 2130706433 or 0177.0.0.1).
        answers = resolver(hostname, port, type=socket.SOCK_STREAM)
        addresses = list(dict.fromkeys(row[4][0] for row in answers))
        if not addresses or any(not _is_public_address(address) for address in addresses):
            raise RequestError("只能下载公网图片地址；本机、内网和保留地址不支持。")
    except RequestError:
        raise
    except (ValueError, UnicodeError, OSError) as exc:
        raise RequestError("图片地址无法解析，或使用了不支持的端口。") from exc
    target = quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~")
    if parsed.query:
        target += "?" + quote(parsed.query, safe="/?%:@!$&'()*+,;=-._~[]")
    return parsed, hostname, port, addresses, target


class PinnedConnection(http.client.HTTPConnection):
    """Never resolve the hostname a second time when opening the socket."""
    def __init__(self, hostname, port, address, secure):
        super().__init__(hostname, port, timeout=NETWORK_TIMEOUT)
        self.address = address
        self.secure = secure

    def connect(self):
        self.sock = socket.create_connection((self.address, self.port), self.timeout)
        if self.secure:
            try:
                self.sock = ssl.create_default_context().wrap_socket(self.sock, server_hostname=self.host)
            except Exception:
                self.sock.close()
                raise


def download_image(url, *, resolver=None, connection_factory=PinnedConnection):
    """Fetch a public direct image, without cookies, credentials or proxy routing.

    Protected artwork pages are deliberately not scraped. Redirects get the
    same DNS/IP checks as the initial request; TLS retains the source hostname.
    """
    current = url
    started = time.monotonic()
    for redirect_count in range(6):
        parsed, hostname, port, addresses, target = public_target(
            current, resolver=resolver or socket.getaddrinfo)
        connection = connection_factory(hostname, port, addresses[0], parsed.scheme == "https")
        try:
            connection.request("GET", target, headers={
                "User-Agent": "SoraMaterialStudio/1.0 (user-initiated image import)",
                "Accept": "image/avif,image/webp,image/png,image/jpeg,image/gif,application/octet-stream;q=0.5",
                "Accept-Encoding": "identity",
                "Connection": "close",
            })
            response = connection.getresponse()
            if response.status in REDIRECT_STATUSES:
                location = response.getheader("Location")
                if not location or redirect_count >= 5:
                    raise RequestError("图片地址重定向次数过多或缺少目标地址。")
                current = urljoin(current, location)
                continue
            if response.status != 200:
                raise RequestError(f"图片下载失败（HTTP {response.status}）。{LOCAL_IMPORT_HINT}")
            content_type = (response.getheader("Content-Type") or "").split(";", 1)[0].strip().lower()
            if content_type in {"text/html", "application/xhtml+xml", "application/json", "text/plain"}:
                raise RequestError("这里返回的是网页或登录提示，不是图片直链。" + LOCAL_IMPORT_HINT)
            encoding = (response.getheader("Content-Encoding") or "identity").lower()
            if encoding != "identity":
                raise RequestError("图片服务器返回了不支持的压缩传输，请改用本地原图导入。")
            declared = response.getheader("Content-Length")
            if declared:
                try:
                    if int(declared) < 0 or int(declared) > MAX_IMAGE_BYTES:
                        raise RequestError("单张图片不能超过 80 MB。", 413)
                except ValueError as exc:
                    if isinstance(exc, RequestError):
                        raise
                    raise RequestError("图片服务器返回了无效的文件大小。") from exc
            chunks, total = [], 0
            while True:
                if time.monotonic() - started > 90:
                    raise RequestError("下载超时，请下载到本地后导入。")
                chunk = response.read(min(65536, MAX_IMAGE_BYTES + 1 - total))
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_IMAGE_BYTES:
                    raise RequestError("单张图片不能超过 80 MB。", 413)
                chunks.append(chunk)
            content = b"".join(chunks)
            if not content:
                raise RequestError("图片服务器返回了空文件。")
            if content.lstrip()[:32].lower().startswith((b"<!doctype", b"<html", b"<?xml")):
                raise RequestError("这个地址返回网页而非原图。" + LOCAL_IMPORT_HINT)
            name = unquote(parsed.path.rsplit("/", 1)[-1]) or "downloaded-image"
            return content, name
        except RequestError:
            raise
        except (OSError, http.client.HTTPException) as exc:
            raise RequestError("无法下载这张图片。" + LOCAL_IMPORT_HINT) from exc
        finally:
            connection.close()
    raise RequestError("图片地址重定向次数过多。")


def git_state(root):
    """Never ask Git to output credentials or remotes with embedded passwords."""
    try:
        publisher = importlib.import_module("publish")
        status = getattr(publisher, "status", None)
        if status:
            result = status()
            if isinstance(result, dict):
                return result
    except Exception:
        pass
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
    def run(*args):
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=10, **flags)
    try:
        branch = run("branch", "--show-current")
        if branch.returncode:
            return {"available": False, "status": "当前目录尚未接入 Git 仓库。", "dirty": False}
        result = run("status", "--short")
        remote = run("remote", "get-url", "origin").stdout.strip()
        if "://" in remote and "@" in remote:
            parsed = urlsplit(remote)
            remote = parsed.scheme + "://" + (parsed.hostname or "") + parsed.path
        return {"available": True, "branch": branch.stdout.strip(), "remote": remote,
                "dirty": bool(result.stdout.strip()), "status": result.stdout.strip() or "没有未提交的修改"}
    except (OSError, subprocess.TimeoutExpired):
        return {"available": False, "status": "暂时无法读取 Git 状态。", "dirty": False}


class StudioServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, root=ROOT, port=0, token=None, core=None):
        self.root = Path(root).resolve()
        self.token = token or secrets.token_urlsafe(32)
        self.core = core or importlib.import_module("studio_core")
        self.jobs = {}
        self.jobs_lock = threading.RLock()
        self.operation_lock = self.core.LOCK
        super().__init__(("127.0.0.1", port), StudioHandler)

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server_port}"

    def snapshot(self):
        with self.operation_lock:
            value = self.core.state()
            value["git"] = git_state(self.root)
            return value

    def start_job(self, action, payload):
        script = "build_preview.py" if action == "build" else "publish.py"
        if not (self.root / script).is_file():
            raise RequestError("此操作所需的脚本尚未安装完成。", 503)
        command = [sys.executable, str(self.root / script)]
        if action != "build":
            command.append(action)
        if action == "publish":
            message = payload.get("message", "")
            if not isinstance(message, str) or len(message) > 2000 or "\x00" in message:
                raise RequestError("提交说明最多 2000 字。")
            if message.strip():
                command.extend(["--message", message.strip()])
        with self.jobs_lock:
            if any(job["status"] == "running" for job in self.jobs.values()):
                raise RequestError("已有构建或发布正在进行，请等待完成。", 409)
            job_id = secrets.token_hex(12)
            self.jobs[job_id] = {"status": "running", "log": "正在准备…\n", "action": action}
        def work():
            flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
            last_result = {}
            try:
                with self.operation_lock:
                    with subprocess.Popen(command, cwd=self.root, stdout=subprocess.PIPE,
                                          stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                          errors="replace", env={**__import__("os").environ,
                                          "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"}, **flags) as process:
                        for line in process.stdout:
                            try:
                                parsed = json.loads(line)
                                if isinstance(parsed, dict):
                                    last_result = parsed
                            except ValueError:
                                pass
                            with self.jobs_lock:
                                job = self.jobs[job_id]
                                job["log"] = (job["log"] + line)[-120000:]
                        code = process.wait()
                with self.jobs_lock:
                    self.jobs[job_id]["status"] = "success" if code == 0 else "error"
                    self.jobs[job_id]["result"] = {**last_result, "exitCode": code,
                        "restartRequired": last_result.get("restartRequired", action == "sync" and code == 0),
                        "previewUrl": self.url + "/index.html" if action == "build" and code == 0 else None}
            except Exception as exc:
                with self.jobs_lock:
                    self.jobs[job_id]["status"] = "error"
                    self.jobs[job_id]["log"] += "\n操作未完成：" + str(exc)
        threading.Thread(target=work, daemon=True, name="studio-" + action).start()
        return {"jobId": job_id}


class StudioHandler(BaseHTTPRequestHandler):
    server_version = "SoraStudio/1.0"
    protocol_version = "HTTP/1.1"

    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def log_message(self, format, *args):
        # Never log headers, upload paths, tokens, or personal source URLs.
        pass

    def headers_for(self, code, content_type, length, cache="no-store"):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Content-Security-Policy", "frame-ancestors 'none'; object-src 'none'; base-uri 'self'")
        self.end_headers()

    def json(self, value, status=200):
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.headers_for(status, "application/json; charset=utf-8", len(raw))
        if self.command != "HEAD":
            self.wfile.write(raw)

    def fail(self, exc):
        self.close_connection = True
        status = getattr(exc, "status", 400)
        if status == 400 and ("Revision" in type(exc).__name__ or "版本" in str(exc) or "冲突" in str(exc)):
            status = 409
        self.json({"error": str(exc)}, status)

    def check_request(self, api=False):
        hosts = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        if self.headers.get("Host", "").lower() not in hosts:
            raise RequestError("请使用启动器打开的本机工作台地址。", 403)
        origins = {"http://" + host for host in hosts}
        origin = self.headers.get("Origin")
        if origin is not None and origin not in origins:
            raise RequestError("工作台仅接受本机页面发出的请求。", 403)
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            raise RequestError("不接受其他网站发出的请求。", 403)
        if api:
            provided = self.headers.get("X-Studio-Token", "")
            if not hmac.compare_digest(provided.encode("utf-8"), self.server.token.encode("utf-8")):
                raise RequestError("工作台连接已失效，请重新使用启动器打开。", 401)

    def read_body(self, limit, too_large="上传内容过大，单张图片最多 80 MB。"):
        if self.headers.get("Transfer-Encoding"):
            raise RequestError("不支持分块上传，请重新选择文件。")
        length = self.headers.get("Content-Length")
        if length is None:
            raise RequestError("请求缺少文件大小。", 411)
        try:
            length = int(length)
        except ValueError as exc:
            raise RequestError("请求文件大小无效。") from exc
        if length < 0 or length > limit:
            raise RequestError(too_large, 413)
        data = self.rfile.read(length)
        if len(data) != length:
            raise RequestError("上传未完成，请重新选择文件。")
        return data

    def read_json(self):
        if self.headers.get_content_type() != "application/json":
            raise RequestError("请求必须使用 JSON 格式。", 415)
        try:
            value = json.loads(self.read_body(MAX_JSON_BYTES), parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        except (UnicodeDecodeError, ValueError) as exc:
            if isinstance(exc, RequestError):
                raise
            raise RequestError("请求内容格式有误。") from exc
        if not isinstance(value, dict):
            raise RequestError("请求内容必须是一个对象。")
        return value

    def do_GET(self):
        try:
            route = urlsplit(self.path).path
            api = route.startswith("/api/")
            self.check_request(api=api)
            if route == "/api/state":
                self.json(self.server.snapshot())
            elif route.startswith("/api/job/"):
                with self.server.jobs_lock:
                    value = copy.deepcopy(self.server.jobs.get(route.rsplit("/", 1)[-1]))
                if value is None:
                    raise RequestError("这个操作记录已不存在，请刷新工作台。", 404)
                self.json(value)
            elif api:
                raise RequestError("不存在的接口。", 404)
            else:
                self.static_file(route)
        except (RequestError, ValueError, KeyError, OSError) as exc:
            self.fail(exc)

    def do_HEAD(self):
        self.do_GET()

    def do_POST(self):
        try:
            parsed = urlsplit(self.path)
            self.check_request(api=True)
            if parsed.path == "/api/pixiv-metadata":
                filename = parse_qs(parsed.query).get("filename", [""])[0]
                if not filename or len(filename) > 500 or Path(filename).suffix.lower() not in {".json", ".html", ".htm"}:
                    raise RequestError("请选择 JSON 作品资料或已保存的 HTML 作品页面。")
                content = self.read_body(MAX_METADATA_BYTES, "作品资料最多 8 MB，请分成较小文件后导入。")
                importer = importlib.import_module("import_pixiv_local")
                with self.server.operation_lock:
                    result = importer.update_metadata_bytes(content, filename,
                        self.headers.get("X-Studio-Revision", ""), engine=self.server.core)
                    self.json(result)
                return
            if parsed.path == "/api/upload":
                filename = parse_qs(parsed.query).get("filename", ["image"])[0]
                if len(filename) > 500:
                    raise RequestError("原文件名过长，请选择其他文件。")
                content = self.read_body(MAX_IMAGE_BYTES)
                with self.server.operation_lock:
                    self.json(self.server.core.stage_upload(content, filename))
                return
            payload = self.read_json()
            if parsed.path in {"/api/build", "/api/package", "/api/publish", "/api/sync"}:
                self.json(self.server.start_job(parsed.path.rsplit("/", 1)[-1], payload), 202)
                return
            if parsed.path == "/api/import":
                urls = payload.pop("urls", [])
                uploads = payload.get("uploadIds", [])
                if not isinstance(urls, list) or len(urls) > MAX_URLS or not all(isinstance(url, str) for url in urls):
                    raise RequestError("每批最多下载 25 张图片，请逐行填写图片直链。")
                if not isinstance(uploads, list) or not all(isinstance(value, str) for value in uploads):
                    raise RequestError("所选图片记录格式有误，请重新选择。")
                payload["uploadIds"] = list(uploads)
                payload["downloadSources"] = {}
                total = 0
                for url in urls:
                    content, name = download_image(url)
                    total += len(content)
                    if total > MAX_BATCH_BYTES:
                        raise RequestError("这批图片超过 512 MB，请分批导入。", 413)
                    with self.server.operation_lock:
                        staged = self.server.core.stage_upload(content, name)
                    payload["uploadIds"].append(staged["uploadId"])
                    payload["downloadSources"][staged["uploadId"]] = url
                method = self.server.core.import_assets
            elif parsed.path == "/api/record":
                method = self.server.core.save_record
            elif parsed.path == "/api/replace":
                method = self.server.core.replace_asset
            else:
                raise RequestError("不存在的接口。", 404)
            with self.server.operation_lock:
                self.json(method(payload))
        except (RequestError, ValueError, KeyError, TypeError, OSError) as exc:
            self.fail(exc)

    def do_OPTIONS(self):
        self.fail(RequestError("工作台不接受跨网站调用。", 403))

    def static_file(self, route):
        path = unquote(route)
        if path == "/":
            path = "/studio.html"
        allowed_files = {"/index.html", "/studio.html", "/studio.js", "/studio.css", "/rotation-engine.js"}
        media_allowed = path.startswith("/media/") and Path(path).suffix.lower() in {
            ".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif"}
        if "\\" in path or "\x00" in path or not (path in allowed_files or media_allowed):
            raise RequestError("文件不存在。", 404)
        target = (self.server.root / path.lstrip("/")).resolve()
        if not target.is_relative_to(self.server.root) or not target.is_file():
            raise RequestError("文件不存在；如果尚未生成预览，请先点击“生成预览”。", 404)
        # Resolving a symlink inside media must never grant access to another
        # location in the project (backups, credentials, server code, etc.).
        if media_allowed and not target.is_relative_to((self.server.root / "media").resolve()):
            raise RequestError("文件不存在。", 404)
        mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if mime.startswith("text/") or mime == "application/javascript":
            mime += "; charset=utf-8"
        self.headers_for(200, mime, target.stat().st_size,
                         cache="private, max-age=300" if media_allowed else "no-store")
        if self.command != "HEAD":
            with target.open("rb") as stream:
                while block := stream.read(128 * 1024):
                    self.wfile.write(block)


def create_server(root=ROOT, port=0, token=None, core=None):
    return StudioServer(root=root, port=port, token=token, core=core)


def main():
    parser = argparse.ArgumentParser(description="空想便笺 · 本地素材工作台")
    parser.add_argument("--port", type=int, default=0, help="本机端口；默认自动选择空闲端口")
    parser.add_argument("--no-browser", action="store_true", help="只启动服务，不自动打开浏览器")
    args = parser.parse_args()
    core = importlib.import_module("studio_core")
    core.initialize()
    with create_server(port=args.port, core=core) as server:
        url = server.url + "/studio.html#token=" + server.token
        print("素材工作台已就绪，仅在本机运行。")
        print(server.url + "/studio.html", flush=True)
        print("关闭这个窗口会停止工作台；素材与网页已保存到项目目录。", flush=True)
        if not args.no_browser:
            webbrowser.open(url)
        try:
            server.serve_forever(poll_interval=0.25)
        except KeyboardInterrupt:
            print("\n工作台已关闭。")


if __name__ == "__main__":
    main()
