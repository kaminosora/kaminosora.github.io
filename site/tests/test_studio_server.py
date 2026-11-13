"""Boundary tests: local authorization, static isolation and public-image imports."""
import contextlib
import http.client
import io
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import studio_server as server
import import_pixiv_local as pixiv_importer


class FakeCore:
    LOCK = threading.RLock()

    def __init__(self):
        self.calls = []

    def state(self):
        return {"library": {"ips": [], "assets": []}, "revision": "revision-1", "meta": {}}

    def stage_upload(self, content, filename):
        self.calls.append(("upload", content, filename))
        return {"uploadId": "staged", "name": filename, "width": 2, "height": 2, "bytes": len(content)}

    def save_record(self, payload):
        self.calls.append(("record", payload))
        if payload.get("revision") != "revision-1":
            raise ValueError("版本已改变，请刷新后重试。")
        return self.state()

    def import_assets(self, payload):
        self.calls.append(("import", payload))
        return self.state()

    def replace_asset(self, payload):
        self.calls.append(("replace", payload))
        return self.state()


class ServerBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        (self.root / "studio.html").write_text("studio", encoding="utf-8")
        (self.root / "index.html").write_text("preview", encoding="utf-8")
        (self.root / "rotation-engine.js").write_text("engine", encoding="utf-8")
        (self.root / "content").mkdir()
        (self.root / "content" / "private.json").write_text('{"secret":true}', encoding="utf-8")
        (self.root / "media").mkdir()
        (self.root / "media" / "image.png").write_bytes(b"fake image")
        self.core = FakeCore()
        self.httpd = server.create_server(root=self.root, core=self.core, token="test-token")
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
        self.directory.cleanup()

    def request(self, method, path, data=None, headers=None, auth=True):
        connection = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=3)
        final_headers = {"X-Studio-Token": "test-token"} if auth else {}
        if data is not None and not isinstance(data, (bytes, str)):
            data = json.dumps(data).encode()
            final_headers["Content-Type"] = "application/json"
        final_headers.update(headers or {})
        connection.request(method, path, body=data, headers=final_headers)
        response = connection.getresponse()
        result = (response.status, dict(response.getheaders()), response.read())
        connection.close()
        return result

    def test_api_requires_token_for_read_and_write(self):
        self.assertEqual(self.request("GET", "/api/state", auth=False)[0], 401)
        self.assertEqual(self.request("POST", "/api/record", {"revision": "revision-1"}, auth=False)[0], 401)
        with patch.object(server, "git_state", return_value={"available": False}):
            status, headers, body = self.request("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["revision"], "revision-1")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertEqual(self.core.calls, [])

    def test_host_origin_and_cross_site_rejected_even_with_token(self):
        for headers in ({"Host": "attacker.invalid"}, {"Origin": "https://attacker.invalid"},
                        {"Origin": "null"}, {"Sec-Fetch-Site": "cross-site"}):
            with self.subTest(headers=headers):
                self.assertEqual(self.request("POST", "/api/record", {"revision": "revision-1"}, headers)[0], 403)
        self.assertEqual(self.core.calls, [])

    def test_static_allowlist_keeps_private_and_source_files_out(self):
        for path in ("/content/private.json", "/studio_server.py", "/.git/config", "/generated/asset-index.json",
                     "/media/../content/private.json", "/media/%2e%2e/content/private.json", "/media/%5c..%5csecret.png"):
            with self.subTest(path=path):
                self.assertEqual(self.request("GET", path, auth=False)[0], 404)
        self.assertEqual(self.request("GET", "/", auth=False)[2], b"studio")
        self.assertEqual(self.request("GET", "/index.html", auth=False)[2], b"preview")
        self.assertEqual(self.request("GET", "/rotation-engine.js", auth=False)[2], b"engine")
        self.assertEqual(self.request("GET", "/media/image.png", auth=False)[2], b"fake image")

    def test_upload_passes_original_bytes_and_name_without_writing_arbitrary_path(self):
        raw = b"\x89PNG\r\n\x1a\noriginal"
        status, _, body = self.request("POST", "/api/upload?filename=%E5%9B%BE%E7%89%87.png", raw)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["uploadId"], "staged")
        self.assertEqual(self.core.calls, [("upload", raw, "图片.png")])

    def test_oversized_upload_rejected_before_reading_body(self):
        status, _, _ = self.request("POST", "/api/upload?filename=a.png", b"",
                                     {"Content-Length": str(server.MAX_IMAGE_BYTES + 1)})
        self.assertEqual(status, 413)
        self.assertEqual(self.core.calls, [])

    def test_offline_metadata_upload_has_auth_limit_and_separate_parser(self):
        content = b'[{"id":"123456","title":"Original","description":"","tags":[]}]'
        path = '/api/pixiv-metadata?filename=works.json'
        with patch.object(pixiv_importer,'update_metadata_bytes',return_value={'updated':2,'updatedWorks':1}) as update, \
             patch.object(server,'download_image',side_effect=AssertionError('No network allowed')):
            self.assertEqual(self.request('POST',path,content,auth=False)[0],401)
            self.assertEqual(self.request('POST',path,content,{'Origin':'https://example.com'})[0],403)
            self.assertEqual(self.request('POST',path,b'',{'Content-Length':str(server.MAX_METADATA_BYTES+1)})[0],413)
            self.assertEqual(self.request('POST','/api/pixiv-metadata?filename=works.exe',content)[0],400)
            update.assert_not_called()
            status,_,body = self.request('POST',path,content,{'X-Studio-Revision':'revision-1'})
            self.assertEqual(status,200)
            self.assertEqual(json.loads(body)['updatedWorks'],1)
            update.assert_called_once_with(content,'works.json','revision-1',engine=self.core)
        self.assertEqual(self.core.calls,[])

    def test_offline_metadata_revision_failure_is_reported(self):
        with patch.object(pixiv_importer,'update_metadata_bytes',side_effect=ValueError('版本已改变，请刷新后重试。')):
            status,_,body = self.request('POST','/api/pixiv-metadata?filename=saved.html',b'<html></html>')
        self.assertEqual(status,409)
        self.assertIn('版本',json.loads(body)['error'])

    def test_json_validation_and_revision_conflict(self):
        self.assertEqual(self.request("POST", "/api/record", "{}", {"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.request("POST", "/api/record", "{bad", {"Content-Type": "application/json"})[0], 400)
        self.assertEqual(self.request("POST", "/api/record", "[]", {"Content-Type": "application/json"})[0], 400)
        self.assertEqual(self.request("POST", "/api/record", '{"weight":NaN}', {"Content-Type": "application/json"})[0], 400)
        self.assertEqual(self.request("POST", "/api/record", {"revision": "old"})[0], 409)

    def test_url_import_stages_all_images_before_committing(self):
        payload = {"revision": "revision-1", "common": {"label": "Test"}, "uploadIds": [],
                   "urls": ["https://images.example/a.png", "https://images.example/b.png"]}
        with patch.object(server, "download_image", side_effect=[(b"one", "a.png"), server.RequestError("failed")]):
            self.assertEqual(self.request("POST", "/api/import", payload)[0], 400)
        self.assertEqual([item[0] for item in self.core.calls], ["upload"])

    def test_unknown_job_and_cross_origin_options(self):
        self.assertEqual(self.request("GET", "/api/job/missing")[0], 404)
        self.assertEqual(self.request("OPTIONS", "/api/record")[0], 403)

    def test_job_arguments_do_not_become_shell_commands(self):
        script = "import json, sys\nprint(json.dumps(sys.argv[1:]))\n"
        (self.root / "publish.py").write_text(script, encoding="utf-8")
        message = "Update & echo injected > surprise.txt"
        status, _, body = self.request("POST", "/api/publish", {"message": message})
        self.assertEqual(status, 202)
        job_id = json.loads(body)["jobId"]
        for _ in range(60):
            value = json.loads(self.request("GET", "/api/job/" + job_id)[2])
            if value["status"] != "running":
                break
            time.sleep(0.05)
        self.assertEqual(value["status"], "success", value)
        self.assertIn(json.dumps(["publish", "--message", message]), value["log"])
        self.assertFalse((self.root / "surprise.txt").exists())

    def test_sync_job_preserves_publisher_restart_result(self):
        script="import json\nprint(json.dumps({'status':'success','restartRequired':False,'commit':'abc123'}))\n"
        (self.root/'publish.py').write_text(script,encoding='utf-8')
        status,_,body=self.request('POST','/api/sync',{})
        self.assertEqual(status,202)
        job_id=json.loads(body)['jobId']
        for _ in range(60):
            value=json.loads(self.request('GET','/api/job/'+job_id)[2])
            if value['status']!='running':break
            time.sleep(.05)
        self.assertEqual(value['status'],'success',value)
        self.assertFalse(value['result']['restartRequired'])
        self.assertEqual(value['result']['commit'],'abc123')


def public_dns(host, port, **kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


class FakeResponse:
    def __init__(self, status=200, headers=None, body=b"\x89PNGfake"):
        self.status = status
        self.headers = headers or {"Content-Type": "image/png"}
        self.stream = io.BytesIO(body)

    def getheader(self, name):
        return self.headers.get(name)

    def read(self, size):
        return self.stream.read(size)


class DownloadTests(unittest.TestCase):
    def factory(self, responses):
        calls = []
        class Connection:
            def __init__(self, hostname, port, address, secure):
                calls.append({"hostname": hostname, "port": port, "address": address, "secure": secure})
            def request(self, method, path, headers):
                calls[-1].update(method=method, path=path, headers=headers)
            def getresponse(self):
                return responses.pop(0)
            def close(self):
                calls[-1]["closed"] = True
        return Connection, calls

    def test_private_literal_dns_answers_and_mixed_answers_rejected(self):
        for address in ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "192.168.0.5", "100.64.0.1", "0.0.0.0",
                        "ff02::1", "224.0.0.1", "::ffff:127.0.0.1", "2002:7f00:1::"]:
            def dns(host, port, **kwargs):
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]
            with self.subTest(address=address), self.assertRaises(server.RequestError):
                server.public_target("https://image.example/a.png", resolver=dns)
        def mixed(host, port, **kwargs):
            return public_dns(host, port) + [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]
        with self.assertRaises(server.RequestError):
            server.public_target("https://image.example/a.png", resolver=mixed)

    def test_non_http_credentials_and_unsafe_ports_are_rejected(self):
        for url in ["file:///C:/secret.png", "https://user:pass@example.com/a.png", "https://example.com:8080/a.png",
                    "https://example.com\\@127.0.0.1/a.png", "https://example.com/\r\nInjected:yes"]:
            with self.subTest(url=url), self.assertRaises(server.RequestError):
                server.public_target(url, resolver=public_dns)

    def test_download_uses_pinned_ip_hostname_and_no_login_headers(self):
        connection, calls = self.factory([FakeResponse()])
        data, name = server.download_image("https://image.example/高清.png#token", resolver=public_dns, connection_factory=connection)
        self.assertEqual(name, "高清.png")
        self.assertEqual(data, b"\x89PNGfake")
        self.assertEqual(calls[0]["address"], "93.184.216.34")
        self.assertEqual(calls[0]["hostname"], "image.example")
        self.assertTrue(calls[0]["secure"])
        self.assertEqual(calls[0]["path"], "/%E9%AB%98%E6%B8%85.png")
        self.assertFalse({"Cookie", "Authorization", "Referer"} & set(calls[0]["headers"]))
        self.assertTrue(calls[0]["closed"])

    def test_redirect_to_private_address_never_connects(self):
        connection, calls = self.factory([FakeResponse(302, {"Location": "http://127.0.0.1/secret.png"})])
        def dns(host, port, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1" if host == "127.0.0.1" else "93.184.216.34", port))]
        with self.assertRaises(server.RequestError):
            server.download_image("https://image.example/a.png", resolver=dns, connection_factory=connection)
        self.assertEqual(len(calls), 1)

    def test_login_html_and_unlabelled_html_have_useful_local_import_error(self):
        for response in [FakeResponse(403), FakeResponse(headers={"Content-Type": "text/html"}),
                         FakeResponse(headers={"Content-Type": "application/octet-stream"}, body=b"<!DOCTYPE html><html>login</html>")]:
            connection, _ = self.factory([response])
            with self.subTest(response=response), self.assertRaisesRegex(server.RequestError, "本地"):
                server.download_image("https://image.example/a.png", resolver=public_dns, connection_factory=connection)

    def test_oversized_content_length_and_stream_are_rejected(self):
        for response in [FakeResponse(headers={"Content-Type": "image/png", "Content-Length": "100"}),
                         FakeResponse(body=b"x" * 30)]:
            connection, _ = self.factory([response])
            with self.subTest(response=response), patch.object(server, "MAX_IMAGE_BYTES", 20), self.assertRaises(server.RequestError) as failure:
                server.download_image("https://image.example/a.png", resolver=public_dns, connection_factory=connection)
            self.assertEqual(failure.exception.status, 413)


if __name__ == "__main__":
    unittest.main()
