"""Execute the analysis step after weather validation or deterministic forecasting."""

from backend.openai_analysis import OpenAIAnalyzer
from backend.service import ForecastService
from backend.service import parse_request
from backend.validation import _object, ForecastValidationError
from backend.weather_analysis import fingerprint, stats, summarize_weather
from backend.weather_provider import WeatherProvider, select_weather
from time import perf_counter


class AgentService:
    def __init__(self, forecasts=None, analyzer=None, weather_provider=None):
        self.forecasts = forecasts if forecasts is not None else ForecastService()
        self.analyzer = analyzer if analyzer is not None else OpenAIAnalyzer()
        self.weather_provider = weather_provider if weather_provider is not None else WeatherProvider()

    def weather_analysis(self, payload):
        body = _object(payload, ("weather",), "request", "INVALID_REQUEST")
        summary = summarize_weather(body["weather"])
        return {"input_summary": summary, "analysis": self.analyzer.analyze(summary)}

    def forecast(self, payload):
        refresh = payload.get("refresh_weather", False) if isinstance(payload, dict) else False
        if type(refresh) is not bool:
            raise ForecastValidationError("INVALID_REQUEST", "request.refresh_weather", "Expected a boolean.")
        base = {k: v for k, v in payload.items() if k != "refresh_weather"} if isinstance(payload, dict) else payload
        request, _, _ = parse_request(base, True)
        provided = "weather" in base
        if provided and refresh:
            raise ForecastValidationError("INVALID_REQUEST", "request.refresh_weather", "Cannot refresh explicitly provided weather.")
        cycle = []

        def step(name, function):
            started = perf_counter()
            result = function()
            cycle.append({"step": name, "duration_ms": round((perf_counter() - started) * 1000, 2)})
            return result

        if provided:
            weather, source = base["weather"], {"mode": "provided", "provenance_sha256": None}
        else:
            weather, source = step("refresh_weather" if refresh else "acquire_weather",
                                   lambda: self.weather_provider.resolve(request, refresh))
            weather = select_weather(weather, request)
        forecast = step("prepare_and_forecast", lambda: self.forecasts.forecast({**base, "weather": weather}))
        analysis = step("analyze", lambda: self._analyze_forecast(forecast))
        if not provided and analysis["recommended_action"] == "refresh_weather":
            updated, updated_source = step("agent_refresh_weather", lambda: self.weather_provider.resolve(request, True))
            updated = select_weather(updated, request)
            # Provenance retrieval time alone is not a change in model inputs.
            old_inputs = {k: weather[k] for k in ("run_id", "points", "wind_height_m")}
            new_inputs = {k: updated[k] for k in ("run_id", "points", "wind_height_m")}
            if fingerprint(old_inputs) != fingerprint(new_inputs):
                forecast = step("recalculate", lambda: self.forecasts.forecast({**base, "weather": updated}))
                analysis = step("reanalyze", lambda: self._analyze_forecast(forecast))
                source = updated_source
        return {"forecast": forecast, "analysis": analysis, "cycle": cycle, "weather_source": source}

    def _analyze_forecast(self, forecast):
        facts = {"kind": "computed_forecast", "input_sha256": fingerprint(forecast),
                 "as_of": forecast["as_of"], "horizon_hours": forecast["horizon_hours"],
                 "weather": forecast["weather"], "warnings": forecast["warnings"],
                 "model": forecast["history"]["model"], "turbines": []}
        for turbine in forecast["turbine_ids"]:
            points = [p for p in forecast["points"] if p["turbine_id"] == turbine]
            facts["turbines"].append({"turbine_id": turbine,
                "normalized_power": stats([p["normalized_power"] for p in points]),
                "wind_speed_ms": stats([p["wind_speed_ms"] for p in points]),
                "extrapolated_hours": sum(p["extrapolated"] for p in points), "hours": len(points)})
        return self.analyzer.analyze(facts)
