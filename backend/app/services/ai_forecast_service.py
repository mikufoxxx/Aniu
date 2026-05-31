from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
import json
import math
from statistics import mean, pstdev
from typing import Any

from app.core.config import get_settings
from app.services.chart_data_service import chart_data_service
from app.services.llm_service import llm_service


DEFAULT_FORECAST_MODELS = [
    "gpt-5.5",
    "deepseek-v4-flash",
    "grok-4.20-0309-non-reasoning",
    "minimax-m2.7",
    "deepseek-v4-pro",
]


class AIForecastService:
    def analyze_order(
        self,
        *,
        order_id: int,
        symbol: str,
        name: str,
        action: str,
        price: float,
        quantity: int,
        price_series: list[dict[str, Any]],
    ) -> dict[str, Any]:
        technical_context = self.technical_context(price_series)
        quantitative_summary = self.quantitative_summary(
            symbol=symbol,
            name=name,
            action=action,
            technical_context=technical_context,
        )
        models = self._configured_models()
        model_forecasts = self.model_forecasts(
            models=models,
            order_id=order_id,
            symbol=symbol,
            name=name,
            action=action,
            price=price,
            quantity=quantity,
            price_series=price_series,
            technical_context=technical_context,
            quantitative_summary=quantitative_summary,
        )
        return {
            "order_id": order_id,
            "symbol": symbol,
            "name": name,
            "action": action,
            "technical_context": technical_context,
            "quantitative_summary": quantitative_summary,
            "methodology": [
                "日线趋势：MA5/MA20、多周期动量、线性回归斜率",
                "风险区间：近20日波动率、支撑压力、波动锥上下沿",
                "择时指标：RSI14、MACD柱、量能放大与价格位置",
                "交易计划：买入区、目标区、止损位、失效条件",
            ],
            "model_forecasts": model_forecasts,
            "generated_at": datetime.now(UTC).isoformat(),
        }

    def technical_context(self, price_series: list[dict[str, Any]]) -> dict[str, Any]:
        closes = [float(item.get("close") or 0) for item in price_series if float(item.get("close") or 0) > 0]
        if not closes:
            return {
                "direction": "neutral",
                "confidence": 0,
                "last_close": None,
                "support": None,
                "resistance": None,
            }
        returns = [
            (closes[index] - closes[index - 1]) / closes[index - 1]
            for index in range(1, len(closes))
            if closes[index - 1] > 0
        ]
        recent_returns = returns[-20:]
        support_resistance = chart_data_service.support_resistance(price_series[-60:])
        last = price_series[-1]
        last_close = closes[-1]
        ma5 = self._to_float(last.get("ma5"))
        ma20 = self._to_float(last.get("ma20"))
        momentum_5d = self._window_return(closes, 5)
        momentum_20d = self._window_return(closes, 20)
        volatility_20d = pstdev(recent_returns) if len(recent_returns) > 1 else 0.0
        regression_slope = self._linear_regression_slope(closes[-20:])
        ma_spread = ((ma5 - ma20) / last_close) if ma5 and ma20 and last_close > 0 else 0.0
        rsi14 = self._to_float(last.get("rsi14"))
        macd_hist = self._to_float(last.get("macd_hist"))
        amount_values = [float(item.get("amount") or 0) for item in price_series[-20:]]
        amount_average = mean(amount_values[:-1]) if len(amount_values) > 1 else 0.0
        amount_ratio = amount_values[-1] / amount_average if amount_average > 0 else 1.0
        trend_score = (
            momentum_5d * 160
            + momentum_20d * 70
            + regression_slope * 900
            + ma_spread * 110
            + (macd_hist / last_close * 80 if last_close > 0 else 0.0)
        )
        if rsi14 >= 75:
            trend_score -= 8
        elif rsi14 <= 30:
            trend_score += 8
        direction = "bullish" if trend_score >= 8 else "bearish" if trend_score <= -8 else "neutral"
        confidence = min(92, max(38, 54 + abs(trend_score) * 1.25 - volatility_20d * 180))
        support = self._to_float(support_resistance.get("support"))
        resistance = self._to_float(support_resistance.get("resistance"))
        target_price = self._target_price(
            last_close=last_close,
            direction=direction,
            volatility=volatility_20d,
            resistance=resistance,
            support=support,
        )
        stop_loss = self._stop_loss(
            last_close=last_close,
            direction=direction,
            volatility=volatility_20d,
            support=support,
        )
        return {
            "direction": direction,
            "confidence": round(confidence, 2),
            "last_close": round(last_close, 4),
            "ma5": round(ma5, 4) if ma5 else None,
            "ma20": round(ma20, 4) if ma20 else None,
            "ma_spread_pct": round(ma_spread * 100, 4),
            "momentum_5d_pct": round(momentum_5d * 100, 4),
            "momentum_20d_pct": round(momentum_20d * 100, 4),
            "regression_slope_pct": round(regression_slope * 100, 4),
            "volatility_20d_pct": round(volatility_20d * 100, 4),
            "rsi14": round(rsi14, 4) if rsi14 else None,
            "macd_hist": round(macd_hist, 6) if macd_hist else None,
            "amount_ratio": round(amount_ratio, 4),
            "support": round(support, 4) if support else None,
            "resistance": round(resistance, 4) if resistance else None,
            "target_price": round(target_price, 4),
            "stop_loss": round(stop_loss, 4),
            "buy_zone": self._zone(last_close * 0.985, last_close * 1.012),
            "sell_zone": self._zone(target_price * 0.985, target_price * 1.018),
        }

    def quantitative_summary(
        self,
        *,
        symbol: str,
        name: str,
        action: str,
        technical_context: dict[str, Any],
    ) -> str:
        direction_label = self._direction_label(str(technical_context.get("direction") or "neutral"))
        return (
            f"{name} {symbol} 当前量化底稿偏{direction_label}，"
            f"MA价差 {technical_context.get('ma_spread_pct')}%，"
            f"5日/20日动量 {technical_context.get('momentum_5d_pct')}%/"
            f"{technical_context.get('momentum_20d_pct')}%，"
            f"20日波动 {technical_context.get('volatility_20d_pct')}%，"
            f"支撑 {technical_context.get('support')}、压力 {technical_context.get('resistance')}。"
            f"本次模拟动作是 {action}，AI 需要围绕买卖区间、失效条件和风险收益比给出独立判断。"
        )

    def model_forecasts(
        self,
        *,
        models: list[str],
        order_id: int,
        symbol: str,
        name: str,
        action: str,
        price: float,
        quantity: int,
        price_series: list[dict[str, Any]],
        technical_context: dict[str, Any],
        quantitative_summary: str,
    ) -> list[dict[str, Any]]:
        settings = get_settings()
        base_url = str(settings.forecast_ai_base_url or settings.openai_base_url or "").strip()
        api_key = str(settings.forecast_ai_api_key or settings.openai_api_key or "").strip()
        fallback_cards = {
            model: self._fallback_card(model=model, technical_context=technical_context, status="quant_fallback")
            for model in models
        }
        if not base_url or not api_key:
            return list(fallback_cards.values())

        prompt_payload = self._prompt_payload(
            order_id=order_id,
            symbol=symbol,
            name=name,
            action=action,
            price=price,
            quantity=quantity,
            price_series=price_series,
            technical_context=technical_context,
            quantitative_summary=quantitative_summary,
        )
        results: dict[str, dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=min(5, max(1, len(models)))) as executor:
            futures = {
                executor.submit(
                    self._call_model,
                    base_url=base_url,
                    api_key=api_key,
                    model=model,
                    prompt_payload=prompt_payload,
                    technical_context=technical_context,
                ): model
                for model in models
            }
            for future in as_completed(futures):
                model = futures[future]
                try:
                    results[model] = future.result()
                except Exception:
                    results[model] = self._fallback_card(
                        model=model,
                        technical_context=technical_context,
                        status="model_error_fallback",
                    )
        return [results.get(model) or fallback_cards[model] for model in models]

    def _call_model(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        prompt_payload: dict[str, Any],
        technical_context: dict[str, Any],
    ) -> dict[str, Any]:
        payload = {
            "model": model,
            "temperature": 0.2,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是专业A股短线/波段交易研究员。必须只返回 JSON 对象，"
                        "不得输出 Markdown。分析要基于趋势、均线、动量、波动率、支撑压力、"
                        "量价关系和风险收益比。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(prompt_payload, ensure_ascii=False, default=str),
                },
            ],
        }
        response = llm_service._call_llm(
            base_url=base_url,
            api_key=api_key,
            payload=payload,
            timeout_seconds=60,
        )
        raw = self._extract_json(response)
        return self._normalize_card(
            model=model,
            raw=raw,
            technical_context=technical_context,
            status="live_ai",
        )

    def _prompt_payload(
        self,
        *,
        order_id: int,
        symbol: str,
        name: str,
        action: str,
        price: float,
        quantity: int,
        price_series: list[dict[str, Any]],
        technical_context: dict[str, Any],
        quantitative_summary: str,
    ) -> dict[str, Any]:
        compact_series = [
            {
                "trade_date": item.get("trade_date"),
                "open": item.get("open"),
                "high": item.get("high"),
                "low": item.get("low"),
                "close": item.get("close"),
                "amount": item.get("amount"),
                "ma5": item.get("ma5"),
                "ma20": item.get("ma20"),
                "rsi14": item.get("rsi14"),
                "macd_hist": item.get("macd_hist"),
            }
            for item in price_series[-60:]
        ]
        return {
            "order": {
                "id": order_id,
                "symbol": symbol,
                "name": name,
                "action": action,
                "price": price,
                "quantity": quantity,
            },
            "technical_context": technical_context,
            "quantitative_summary": quantitative_summary,
            "recent_price_series": compact_series,
            "output_schema": {
                "direction": "bullish, neutral or bearish",
                "confidence": "0-100 number",
                "target_price": "number",
                "stop_loss": "number",
                "support": "number",
                "resistance": "number",
                "buy_zone": ["low number", "high number"],
                "sell_zone": ["low number", "high number"],
                "key_points": ["3-4条专业依据"],
                "risk_points": ["2-3条风险或失效条件"],
                "analysis": "100-180字中文分析，说明趋势、量价、关键位和交易计划",
            },
        }

    def _normalize_card(
        self,
        *,
        model: str,
        raw: dict[str, Any],
        technical_context: dict[str, Any],
        status: str,
    ) -> dict[str, Any]:
        fallback = self._fallback_card(model=model, technical_context=technical_context, status=status)
        direction = str(raw.get("direction") or fallback["direction"]).strip().lower()
        if direction not in {"bullish", "neutral", "bearish"}:
            direction = fallback["direction"]
        confidence = self._bounded_float(raw.get("confidence"), fallback=fallback["confidence"], low=0, high=100)
        return {
            **fallback,
            "status": status,
            "direction": direction,
            "direction_label": self._direction_label(direction),
            "confidence": round(confidence, 2),
            "target_price": round(self._bounded_float(raw.get("target_price"), fallback=fallback["target_price"], low=0.01, high=99999), 4),
            "stop_loss": round(self._bounded_float(raw.get("stop_loss"), fallback=fallback["stop_loss"], low=0.01, high=99999), 4),
            "support": round(self._bounded_float(raw.get("support"), fallback=fallback["support"], low=0.01, high=99999), 4),
            "resistance": round(self._bounded_float(raw.get("resistance"), fallback=fallback["resistance"], low=0.01, high=99999), 4),
            "buy_zone": self._normalize_zone(raw.get("buy_zone"), fallback=fallback["buy_zone"]),
            "sell_zone": self._normalize_zone(raw.get("sell_zone"), fallback=fallback["sell_zone"]),
            "key_points": self._string_list(raw.get("key_points"), fallback=fallback["key_points"], limit=4),
            "risk_points": self._string_list(raw.get("risk_points"), fallback=fallback["risk_points"], limit=3),
            "analysis": str(raw.get("analysis") or fallback["analysis"]).strip()[:500],
        }

    def _fallback_card(
        self,
        *,
        model: str,
        technical_context: dict[str, Any],
        status: str,
    ) -> dict[str, Any]:
        direction = str(technical_context.get("direction") or "neutral")
        target_price = float(technical_context.get("target_price") or technical_context.get("last_close") or 0)
        stop_loss = float(technical_context.get("stop_loss") or technical_context.get("last_close") or 0)
        support = float(technical_context.get("support") or stop_loss or 0)
        resistance = float(technical_context.get("resistance") or target_price or 0)
        return {
            "model": model,
            "status": status,
            "direction": direction,
            "direction_label": self._direction_label(direction),
            "confidence": float(technical_context.get("confidence") or 0),
            "target_price": round(target_price, 4),
            "stop_loss": round(stop_loss, 4),
            "support": round(support, 4),
            "resistance": round(resistance, 4),
            "buy_zone": technical_context.get("buy_zone") or self._zone(support, target_price),
            "sell_zone": technical_context.get("sell_zone") or self._zone(target_price, resistance),
            "key_points": [
                f"MA5/MA20价差 {technical_context.get('ma_spread_pct')}%，判断趋势结构。",
                f"5日动量 {technical_context.get('momentum_5d_pct')}%，20日动量 {technical_context.get('momentum_20d_pct')}%。",
                f"RSI14 {technical_context.get('rsi14')}，MACD柱 {technical_context.get('macd_hist')}。",
                f"成交额倍率 {technical_context.get('amount_ratio')}，观察量价是否共振。",
            ],
            "risk_points": [
                f"跌破 {round(stop_loss, 4)} 则本轮预测失效。",
                f"接近压力 {round(resistance, 4)} 后需要确认放量突破。",
                "若大盘或板块转弱，短线胜率会快速下降。",
            ],
            "analysis": (
                f"{model} 当前展示量化底稿：趋势偏{self._direction_label(direction)}，"
                f"参考支撑 {round(support, 4)}、压力 {round(resistance, 4)}，"
                f"目标价 {round(target_price, 4)}，止损 {round(stop_loss, 4)}。"
                "后续应重点观察价格是否站稳短均线并获得成交额配合。"
            ),
        }

    def _configured_models(self) -> list[str]:
        settings = get_settings()
        raw = str(settings.forecast_ai_models or "").strip()
        models = [item.strip() for item in raw.split(",") if item.strip()]
        return models or DEFAULT_FORECAST_MODELS

    def _extract_json(self, response: dict[str, Any]) -> dict[str, Any]:
        content = (
            response.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        if not isinstance(content, str) or not content.strip():
            raise ValueError("AI 预测未返回内容")
        text = content.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:].strip()
        if not text.startswith("{"):
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                text = text[start : end + 1]
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise ValueError("AI 预测不是 JSON 对象")
        return parsed

    def _target_price(
        self,
        *,
        last_close: float,
        direction: str,
        volatility: float,
        resistance: float,
        support: float,
    ) -> float:
        if direction == "bearish":
            return max(0.01, min(last_close * (1 - max(0.015, volatility * 1.6)), support * 1.01))
        if direction == "bullish":
            return max(last_close * (1 + max(0.018, volatility * 2.0)), resistance * 0.995)
        return max(0.01, last_close * (1 + max(-0.01, min(0.012, volatility * 0.4))))

    def _stop_loss(
        self,
        *,
        last_close: float,
        direction: str,
        volatility: float,
        support: float,
    ) -> float:
        if direction == "bearish":
            return max(0.01, last_close * (1 - max(0.012, volatility)))
        return max(0.01, min(support * 0.985, last_close * (1 - max(0.018, volatility * 1.8))))

    def _window_return(self, closes: list[float], window: int) -> float:
        if len(closes) <= window or closes[-window - 1] <= 0:
            return 0.0
        return (closes[-1] - closes[-window - 1]) / closes[-window - 1]

    def _linear_regression_slope(self, values: list[float]) -> float:
        if len(values) < 3 or values[-1] <= 0:
            return 0.0
        x_values = list(range(len(values)))
        x_mean = mean(x_values)
        y_mean = mean(values)
        denominator = sum((x - x_mean) ** 2 for x in x_values)
        if denominator <= 0:
            return 0.0
        slope = sum((x - x_mean) * (y - y_mean) for x, y in zip(x_values, values)) / denominator
        return slope / values[-1]

    def _bounded_float(self, value: Any, *, fallback: float, low: float, high: float) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = fallback
        if math.isnan(number) or math.isinf(number):
            number = fallback
        return min(max(number, low), high)

    def _to_float(self, value: Any) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0.0
        if math.isnan(number) or math.isinf(number):
            return 0.0
        return number

    def _normalize_zone(self, value: Any, *, fallback: list[float]) -> list[float]:
        if isinstance(value, list) and len(value) >= 2:
            return self._zone(self._to_float(value[0]), self._to_float(value[1]))
        return fallback

    def _zone(self, low: float, high: float) -> list[float]:
        low_value = min(float(low or 0), float(high or 0))
        high_value = max(float(low or 0), float(high or 0))
        return [round(max(0.01, low_value), 4), round(max(0.01, high_value), 4)]

    def _string_list(self, value: Any, *, fallback: list[str], limit: int) -> list[str]:
        if not isinstance(value, list):
            return fallback[:limit]
        items = [str(item).strip() for item in value if str(item).strip()]
        return (items or fallback)[:limit]

    def _direction_label(self, direction: str) -> str:
        return {
            "bullish": "看多",
            "bearish": "看空",
            "neutral": "震荡",
        }.get(direction, "震荡")


ai_forecast_service = AIForecastService()
