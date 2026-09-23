"""Exercise actual HTTP sockets and the full service, without forecast mocks."""

from http.client import HTTPConnection
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import unittest

from backend.server import MAX_BODY, create_server
from backend.openai_analysis import OpenAIAnalyzer
from backend.tests.test_analysis import sample
from backend.service import ForecastService
from backend.tests.test_service import request, weather, write_history


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = TemporaryDirectory()
        directory = Path(cls.temp.name)
        write_history(directory)
        cls.server = create_server(("127.0.0.1", 0), ForecastService(directory), analyzer=OpenAIAnalyzer(api_key=""))
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        cls.temp.cleanup()

    def call(self, method, path, body=None, headers=None):
        connection = HTTPConnection(*self.server.server_address, timeout=10)
        try:
            connection.request(method, path, body, headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), json.loads(response.read())
        finally:
            connection.close()

    def test_health_routes_methods_and_cors(self):
        for method, path, headers, status in (
            ("GET", "/api/health", {}, 200), ("GET", "/missing", {}, 404),
            ("GET", "/api/forecasts", {}, 405), ("DELETE", "/api/health", {}, 405),
            ("GET", "/api/health", {"Origin": "https://example.invalid"}, 403),
            ("OPTIONS", "/api/forecasts", {"Origin": "http://localhost:3000"}, 200)):
            with self.subTest(method=method, path=path, headers=headers):
                actual, returned, body = self.call(method, path, headers=headers)
                self.assertEqual(actual, status)
                if status >= 400:
                    self.assertEqual(set(body["error"]), {"code", "field", "message"})
                if method == "OPTIONS":
                    self.assertEqual(returned["Access-Control-Allow-Origin"], "http://localhost:3000")
                if status == 403:
                    self.assertNotIn("Access-Control-Allow-Origin", returned)

    def test_forecast_over_http(self):
        for horizon, turbines in ((24, ["turbine_1", "turbine_2"]), (48, ["turbine_2"])):
            body = request(horizon)
            body["turbine_ids"] = turbines
            batch = weather(horizon)
            batch["points"] = [p for p in batch["points"] if p["turbine_id"] in turbines]
            body["weather"] = batch
            status, _, result = self.call("POST", "/api/forecasts", json.dumps(body), {"Content-Type": "application/json"})
            self.assertEqual(status, 200)
            self.assertEqual(len(result["points"]), horizon * len(turbines))
            self.assertAlmostEqual(result["points"][-1]["normalized_power"], 0.2)

    def test_summary_over_http_and_missing_weather(self):
        body = request()
        del body["horizon_hours"]
        status, _, result = self.call("POST", "/api/history/summary", json.dumps(body), {"Content-Type": "application/json"})
        self.assertEqual(status, 200)
        self.assertEqual(result["turbines"][0]["hours"], 24)
        status, _, result = self.call("POST", "/api/forecasts", json.dumps(request()), {"Content-Type": "application/json"})
        self.assertEqual(status, 503)
        self.assertEqual(result["error"]["code"], "WEATHER_UNAVAILABLE")

    def test_invalid_http_inputs(self):
        cases = [("{}", {}, 415), ("{", {"Content-Type": "application/json"}, 400),
                 ('{"x":1,"x":2}', {"Content-Type": "application/json"}, 400),
                 ('{"x":NaN}', {"Content-Type": "application/json"}, 400),
                 ("{}", {"Content-Type": "application/json"}, 422),
                 ("", {"Content-Type": "application/json", "Content-Length": str(MAX_BODY + 1)}, 413)]
        for body, headers, expected in cases:
            with self.subTest(expected=expected, body=body):
                status, _, response = self.call("POST", "/api/forecasts", body, headers)
                self.assertEqual(status, expected)
                self.assertIn("error", response)

    def test_analysis_errors_over_http(self):
        for path, body in (("/api/analysis/weather", {"weather": sample()}),
                           ("/api/agent/forecasts", {**request(), "weather": weather()})):
            status, _, result = self.call("POST", path, json.dumps(body), {"Content-Type": "application/json"})
            self.assertEqual(status, 503)
            self.assertEqual(result["error"]["code"], "OPENAI_NOT_CONFIGURED")
