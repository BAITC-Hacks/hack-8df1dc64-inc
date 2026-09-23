"""Execute the analysis step after weather validation or deterministic forecasting."""

from backend.openai_analysis import OpenAIAnalyzer
from backend.service import ForecastService
from backend.validation import _object
from backend.weather_analysis import fingerprint, stats, summarize_weather


class AgentService:
    def __init__(self, forecasts=None, analyzer=None):
        self.forecasts = forecasts if forecasts is not None else ForecastService()
        self.analyzer = analyzer if analyzer is not None else OpenAIAnalyzer()

    def weather_analysis(self, payload):
        body = _object(payload, ("weather",), "request", "INVALID_REQUEST")
        summary = summarize_weather(body["weather"])
        return {"input_summary": summary, "analysis": self.analyzer.analyze(summary)}

    def forecast(self, payload):
        forecast = self.forecasts.forecast(payload)
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
        return {"forecast": forecast, "analysis": self.analyzer.analyze(facts)}
