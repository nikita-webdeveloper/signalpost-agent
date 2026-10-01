from __future__ import annotations

import re
from collections import Counter
from typing import Any
from urllib.parse import urlparse


LABELS = ("positive", "neutral", "negative", "mixed")
INDEPENDENT_SOURCE_CLASSES = {
    "customer_review",
    "employee_review",
    "licensed_news",
    "public_news",
    "public_mention",
    "licensed_review",
    "official_notice",
}

# High-precision Norwegian and English business/financial sentiment signals
POSITIVE_SIGNALS = {
    "vekst", "rekord", "overskudd", "gevinst", "kontrakt", "vinner", "vant",
    "oppgang", "økning", "positiv", "suksess", "høyere", "styrket", "avtale",
    "tildelt", "ekspanderer", "lønnsom", "lønnsomhet", "forbedret", "oppkjøp",
    "anbefales", "fornøyd", "utmerket", "topp", "bra", "god", "fantastisk",
    "growth", "record", "profit", "gain", "contract", "won", "increase",
    "positive", "success", "higher", "expanded", "profitable", "improved",
    "recommended", "excellent", "great", "award", "awarded",
}

NEGATIVE_SIGNALS = {
    "tap", "underskudd", "nedgang", "fall", "falt", "konkurs", "oppbud",
    "varsel", "kutt", "kutter", "oppsigelse", "oppsigelser", "svikt", "taper",
    "redusert", "krise", "stans", "stanset", "begjært", "klage", "dårlig",
    "misfornøyd", "kritikk", "skuffet", "skuffende", "tapte", "advarsel",
    "loss", "losses", "deficit", "decline", "fell", "drop", "dropped",
    "bankruptcy", "insolvency", "layoff", "layoffs", "cut", "crisis",
    "failure", "poor", "bad", "disappointed", "complaint", "warning",
}


def _normalize_signals(signals: set[str]) -> set[str]:
    res = set()
    trans = str.maketrans({"æ": "ae", "ø": "o", "å": "a"})
    for s in signals:
        res.add(s.casefold())
        res.add(s.casefold().translate(trans))
    return res


_NORM_POSITIVE_SIGNALS = _normalize_signals(POSITIVE_SIGNALS)
_NORM_NEGATIVE_SIGNALS = _normalize_signals(NEGATIVE_SIGNALS)


def _tokenize_text(text: str) -> list[str]:
    cleaned = str(text or "").casefold()
    cleaned = cleaned.translate(str.maketrans({"æ": "ae", "ø": "o", "å": "a"}))
    return re.findall(r"[a-z0-9]+", cleaned)


def classify_text_sentiment(text: str) -> tuple[str, str]:
    """Deterministic, reproducible sentiment classification for Norwegian & English company evidence.

    Returns (label, model_version).
    """
    token_list = _tokenize_text(text)
    pos_matches = sum(any(sig == t or (len(sig) >= 4 and sig in t) for sig in _NORM_POSITIVE_SIGNALS) for t in token_list)
    neg_matches = sum(any(sig == t or (len(sig) >= 4 and sig in t) for sig in _NORM_NEGATIVE_SIGNALS) for t in token_list)

    if pos_matches > 0 and neg_matches > 0:
        return "mixed", "deterministic_lexicon_v1"
    if pos_matches > neg_matches and pos_matches > 0:
        return "positive", "deterministic_lexicon_v1"
    if neg_matches > pos_matches and neg_matches > 0:
        return "negative", "deterministic_lexicon_v1"
    return "neutral", "deterministic_lexicon_v1"


def sentiment_input_eligibility(item: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons = []
    if not item.get("exact_entity"):
        reasons.append("exact company identity is not verified")
    if item.get("source_class") not in INDEPENDENT_SOURCE_CLASSES:
        reasons.append("source is not an allowed independent class")
    for field in ("source_url", "retrieved_at", "evidence_span", "content_sha256"):
        if not item.get(field):
            reasons.append(f"missing {field}")
    if not (item.get("text") or item.get("evidence_span")):
        reasons.append("missing text")
    return not reasons, reasons


def publishable_sentiment_item(item: dict[str, Any]) -> bool:
    eligible, _ = sentiment_input_eligibility({**item, "text": item.get("text") or item.get("evidence_span")})
    label = item.get("label") or item.get("sentiment_label")
    return bool(eligible and label in LABELS)


def _independent_source_id(item: dict[str, Any]) -> str:
    if item.get("source_id"):
        return str(item["source_id"]).strip().casefold()
    host = (urlparse(str(item.get("source_url") or "")).hostname or "").casefold()
    return host.removeprefix("www.")


def aggregate_company_sentiment(items: list[dict[str, Any]]) -> dict[str, Any]:
    accepted = [item for item in items if publishable_sentiment_item(item)]
    independent_sources = {_independent_source_id(item) for item in accepted}
    independent_sources.discard("")
    rejected = len(items) - len(accepted)
    if len(independent_sources) < 2:
        return {
            "status": "abstain",
            "label": None,
            "accepted_items": len(accepted),
            "rejected_items": rejected,
            "reason": "At least two independently sourced, exact-entity items are required for a company-level rollup.",
        }
    counts = Counter(item.get("label") or item.get("sentiment_label") for item in accepted)
    non_neutral = {label for label in counts if label != "neutral" and counts[label]}
    label = next(iter(non_neutral)) if len(non_neutral) == 1 else "mixed" if len(non_neutral) > 1 else "neutral"
    return {
        "status": "available",
        "label": label,
        "accepted_items": len(accepted),
        "rejected_items": rejected,
        "label_counts": dict(counts),
        "independent_source_ids": sorted(independent_sources),
        "source_urls": sorted({item["source_url"] for item in accepted}),
        "warning": "Contextual dated signal, not a timeless fact about the company.",
    }


def generate_sentiment_prediction(item: dict[str, Any]) -> dict[str, Any]:
    """Generate a deterministic sentiment prediction preserving full provenance and evidence."""
    eligible, reasons = sentiment_input_eligibility({
        **item,
        "text": item.get("text") or item.get("evidence_span"),
    })
    if not eligible:
        raise ValueError(f"Ineligible sentiment input: {', '.join(reasons)}")

    text = str(item.get("text") or item.get("evidence_span") or "")
    metrics = item.get("metrics") or {}
    rating = metrics.get("rating") if isinstance(metrics, dict) else None

    if rating is not None:
        try:
            r = float(rating)
            if 0 < r <= 5.0:
                if r >= 4.0:
                    label = "positive"
                elif r <= 2.0:
                    label = "negative"
                else:
                    label = "neutral"
                model_version = "explicit_star_rating_v1"
            else:
                label, model_version = classify_text_sentiment(text)
        except (ValueError, TypeError):
            label, model_version = classify_text_sentiment(text)
    else:
        label, model_version = classify_text_sentiment(text)

    org_number = str(item.get("organisation_number") or "")
    proof = item.get("identity_proof") or [{"type": "exact_entity_verified", "value": True}]

    return {
        "id": str(item["id"]),
        "organisation_number": org_number,
        "label": label,
        "sentiment_label": label,
        "exact_entity": True,
        "identity_proof": proof,
        "source_class": item["source_class"],
        "source_url": item["source_url"],
        "retrieved_at": item["retrieved_at"],
        "effective_at": item.get("effective_at"),
        "content_sha256": item["content_sha256"],
        "evidence_span": item.get("evidence_span") or text[:1200],
        "sentiment_model_version": model_version,
    }


def calculate_sentiment_metrics(
    gold: list[dict[str, Any]],
    predictions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute precision, recall, F1, accuracy, and support across sentiment labels."""
    gold_ids = [str(item["id"]) for item in gold]
    predicted = {str(item["id"]): item for item in predictions}

    published_items = [
        predicted[item_id]
        for item_id in gold_ids
        if item_id in predicted and (predicted[item_id].get("label") in LABELS or predicted[item_id].get("sentiment_label") in LABELS)
    ]

    total_correct = sum(
        (predicted.get(str(item["id"]), {}).get("label") or predicted.get(str(item["id"]), {}).get("sentiment_label")) == item["label"]
        for item in gold
    )
    wrong_entity = sum(not item.get("exact_entity", False) for item in published_items)
    supported = sum(publishable_sentiment_item(item) for item in published_items)
    company_owned = sum(item.get("source_class") == "company_owned" for item in published_items)

    active_labels = tuple(label for label in LABELS if any(item.get("label") == label for item in gold))
    per_label: dict[str, dict[str, Any]] = {}

    for label in LABELS:
        tp = fp = fn = 0
        for item in gold:
            prediction = predicted.get(str(item["id"]))
            pred_label = (prediction.get("label") or prediction.get("sentiment_label")) if prediction else None
            actual = item.get("label")
            tp += actual == label and pred_label == label
            fp += actual != label and pred_label == label
            fn += actual == label and pred_label != label
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_label[label] = {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "support": sum(item.get("label") == label for item in gold),
        }

    macro_f1 = sum(per_label[label]["f1"] for label in active_labels) / len(active_labels) if active_labels else 0.0
    published = len(published_items)
    exact_entity_precision = (published - wrong_entity) / published if published else 1.0
    evidence_support_rate = supported / published if published else 1.0
    coverage = published / len(gold) if gold else 0.0
    accuracy = total_correct / len(gold) if gold else 0.0

    return {
        "gold_items": len(gold),
        "published_predictions": published,
        "accuracy": accuracy,
        "macro_f1": macro_f1,
        "macro_f1_labels": list(active_labels),
        "per_label": per_label,
        "wrong_entity_predictions": wrong_entity,
        "exact_entity_precision": exact_entity_precision,
        "evidence_supported_predictions": supported,
        "evidence_support_rate": evidence_support_rate,
        "company_owned_predictions": company_owned,
        "coverage": coverage,
    }


def evaluate_predictions(
    gold: list[dict[str, Any]],
    predictions: list[dict[str, Any]],
    *,
    minimum_items: int = 300,
) -> dict[str, Any]:
    if minimum_items < 300:
        raise ValueError("The POC sentiment qualification minimum cannot be lower than 300 items")
    gold_ids = [str(item["id"]) for item in gold]
    prediction_ids = [str(item["id"]) for item in predictions]
    if len(set(gold_ids)) != len(gold_ids):
        raise ValueError("Gold item IDs must be unique")
    if len(set(prediction_ids)) != len(prediction_ids):
        raise ValueError("Prediction item IDs must be unique")
    if any(item.get("label") not in LABELS for item in gold):
        raise ValueError(f"Gold labels must be one of: {', '.join(LABELS)}")

    metrics = calculate_sentiment_metrics(gold, predictions)
    per_label = metrics["per_label"]
    required_labels_present = all(per_label[label]["support"] > 0 for label in ("positive", "neutral", "negative"))
    gate = bool(
        len(gold) >= minimum_items
        and required_labels_present
        and metrics["wrong_entity_predictions"] == 0
        and metrics["company_owned_predictions"] == 0
        and metrics["macro_f1"] >= 0.8
        and per_label["positive"]["precision"] >= 0.9
        and per_label["negative"]["precision"] >= 0.9
        and metrics["evidence_supported_predictions"] == metrics["published_predictions"]
        and metrics["coverage"] is not None
        and metrics["coverage"] >= 0.5
    )

    return {
        **metrics,
        "minimum_items_gate": minimum_items,
        "required_labels_present": required_labels_present,
        "qualification_passed": gate,
        "qualification_tier": "production_qualification",
        "production_scale_gate_passed": bool(gate and len(gold) >= 600 and per_label["mixed"]["support"] > 0),
    }
