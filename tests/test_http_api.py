import io, json, unittest
from tests.base import AppTestCase


def call(app, method, path, token=None, body=None, extra_headers=None):
    raw = json.dumps(body).encode() if body is not None else b""
    path_only, _, query = path.partition("?")
    env = {"REQUEST_METHOD": method, "PATH_INFO": path_only, "QUERY_STRING": query,
           "CONTENT_LENGTH": str(len(raw)), "wsgi.input": io.BytesIO(raw),
           "HTTP_AUTHORIZATION": f"Bearer {token}" if token else ""}
    env.update(extra_headers or {})
    state = {}
    def start(status, headers): state.update(status=status, headers=headers)
    out = b"".join(app(env, start))
    return int(state["status"].split()[0]), json.loads(out)


class HttpApiTest(AppTestCase):
    def test_health_ready_and_auth_errors(self):
        from app.web.server import Application
        app = Application()
        self.assertEqual(call(app, "GET", "/health")[0], 200)
        self.assertEqual(call(app, "GET", "/ready")[0], 200)
        self.assertEqual(call(app, "GET", "/leads")[0], 401)
        self.assertEqual(call(app, "GET", "/leads", "bad")[0], 401)

    def test_unknown_fields_org_smuggling_and_unknown_lead(self):
        from app.web.server import Application
        u = self.make_user("http@example.kg")
        app = Application()
        self.assertEqual(call(app, "GET", "/leads", u["token"], {"org_id": "evil"})[0], 200)  # query ignores URL body
        self.assertEqual(call(app, "POST", "/ingest/demo_kg", u["token"], {"org_id": "evil"})[0], 400)
        self.assertEqual(call(app, "GET", "/leads/not-owned", u["token"])[0], 404)

    def test_idempotency_key_returns_same_job(self):
        from app.web.server import Application
        u = self.make_user("http-idem@example.kg")
        app = Application()
        headers = {"HTTP_IDEMPOTENCY_KEY": "same-request"}
        first = call(app, "POST", "/ingest/demo_kg", u["token"], {"limit": 1}, headers)
        second = call(app, "POST", "/ingest/demo_kg", u["token"], {"limit": 1}, headers)
        self.assertEqual(first[0], 202)
        self.assertEqual(second[0], 202)
        self.assertEqual(first[1]["id"], second[1]["id"])

    def test_ingest_creates_durable_job_without_client_org(self):
        from app.web.server import Application
        u = self.make_user("http-ingest@example.kg")
        status, payload = call(Application(), "POST", "/ingest/demo_kg", u["token"], {"limit": 1})
        self.assertEqual(status, 202)
        self.assertEqual(payload["type"], "ingest_source")
        self.assertEqual(payload["org_id"], u["org_id"])


if __name__ == "__main__": unittest.main()
