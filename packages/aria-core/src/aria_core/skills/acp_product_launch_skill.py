"""ACP product launch — offering complet + promo X/Telegram."""
from __future__ import annotations

import re
from typing import Any

from aria_core.skills.acp_cli import is_acp_available, list_offerings
from aria_core.skills.acp_offering_skill import (
    _parse_price_override,
    _parse_template_key,
    _subscription_update_kw,
    build_offering_payload,
    resolve_template,
)
from aria_core.skills.acp_schema import enrich_json_schema

_LAUNCH_RE = re.compile(
    r"(?:"
    r"lancer\s+(?:le\s+)?produit\s+acp"
    r"|ship\s+produit\s+acp"
    r"|publier\s+produit\s+acp"
    r"|lance\s+(?:le\s+)?produit\s+acp"
    r"|acp\s+product\s+launch"
    r"|lancer\s+workflow\s+acp.+publier"
    r"|réparer\s+offres?\s+acp"
    r"|reparer\s+offres?\s+acp"
    r")",
    re.I,
)
_SOCIAL_RE = re.compile(r"(?i)publier|promo|pub|réseaux|reseaux|social|tweet|x\b")


def wants_acp_product_launch(message: str) -> bool:
    return bool(_LAUNCH_RE.search((message or "").strip()))


def _wants_social_promo(message: str) -> bool:
    text = (message or "").strip()
    if _SOCIAL_RE.search(text):
        return True
    return "lancer produit" in text.lower() or "lance le produit" in text.lower()


def compose_product_tweet(
    *,
    name: str,
    description: str,
    price_usd: float,
    sla_minutes: int,
) -> str:
    """Tweet EN (@Aria_ZHC policy) — product launch."""
    short = (description or name).strip().replace("\n", " ")
    if len(short) > 120:
        short = short[:117] + "..."
    return (
        f"New ACP service live: {name} — {price_usd:.2f} USDC, {sla_minutes}m SLA. "
        f"{short} Hire Aria Vanguard ZHC on Virtuals ACP. Research-grade deliverables only."
    )[:280]


async def _upsert_offering(payload: dict[str, Any]) -> tuple[dict | None, str, str | None]:
    existing, err_list = list_offerings()
    if err_list:
        return None, "list_error", err_list
    name = payload["name"]
    hit = None
    for row in existing or []:
        if str(row.get("name") or "").strip().lower() == name.lower():
            hit = row
            break
    from aria_core.skills.acp_cli import create_offering, update_offering

    if hit:
        oid = str(hit.get("id") or "")
        row, err = update_offering(
            oid,
            description=payload["description"],
            price_value=payload["price_value"],
            sla_minutes=payload["sla_minutes"],
            requirements=payload["requirements"],
            deliverable=payload["deliverable"],
            **_subscription_update_kw(payload),
        )
        return row, "update", err
    row, err = create_offering(**payload)
    return row, "create", err


def _enrich_payload(template: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    name = payload["name"]
    req_desc = str(template.get("requirements_description") or f"Inputs required for {name}.")
    deliv_desc = str(template.get("deliverable_description") or f"Deliverable returned for {name}.")
    payload["requirements"] = enrich_json_schema(
        payload.get("requirements"),
        title=f"{name} — requirements",
        description=req_desc,
    )
    payload["deliverable"] = enrich_json_schema(
        payload.get("deliverable"),
        title=f"{name} — deliverable",
        description=deliv_desc,
    )
    return payload


async def _promote_product(
    *,
    name: str,
    description: str,
    price_usd: float,
    sla_minutes: int,
    lang: str,
    do_social: bool,
) -> dict[str, Any]:
    result: dict[str, Any] = {"x_posted": False, "telegram_notified": False}
    if not do_social:
        return result

    tweet = compose_product_tweet(
        name=name,
        description=description,
        price_usd=price_usd,
        sla_minutes=sla_minutes,
    )
    result["tweet_text"] = tweet

    from aria_core.gateway.x_twitter import is_x_post_configured, post_tweet

    if is_x_post_configured():
        _, note = await post_tweet(tweet, approval_id="acp_product_launch")
        result["x_posted"] = "x.com/" in note.lower() and "/status/" in note.lower()
        result["x_note"] = note
    else:
        result["x_note"] = "X non configuré — brouillon conservé."

    try:
        from aria_core.gateway.telegram_bot import notify_admin

        fr_body = (
            f"🚀 Produit ACP live : {name}\n"
            f"Prix : {price_usd} USDC · SLA {sla_minutes}m\n"
            f"Promo X : {'publié' if result.get('x_posted') else 'brouillon / non configuré'}"
        )
        result["telegram_notified"] = await notify_admin(fr_body)
    except Exception as exc:
        result["telegram_error"] = str(exc)[:200]

    return result


async def execute_product_launch(message: str, lang: str) -> tuple[str, dict]:
    lang_key = "fr" if lang == "fr" else "en"
    if not is_acp_available():
        msg = "ACP — acp-cli introuvable." if lang_key == "fr" else "ACP — acp-cli missing."
        return msg, {"acp": "no_cli"}

    repair_all = bool(re.search(r"(?i)réparer|reparer", message or ""))
    template_key = _parse_template_key(message)
    do_social = _wants_social_promo(message)

    if repair_all and not template_key:
        from aria_core.skills.acp_offering_skill import (
            _infer_service_kind,
            build_adhoc_payload,
            load_offering_templates,
            premium_examples,
            template_dashboard_examples,
        )

        lines = ["Réparation offres ACP (premium) :", ""]
        meta: dict[str, Any] = {"acp": "product_repair", "offerings": [], "examples": {}}
        template_names = set()
        for key, tpl in load_offering_templates().items():
            try:
                payload = build_offering_payload(tpl)
                payload = _enrich_payload(tpl, payload)
            except ValueError as exc:
                lines.append(f"• {key} — skip ({exc})")
                continue
            row, action, err = await _upsert_offering(payload)
            if err or not row:
                lines.append(f"• {key} — erreur {str(err)[:80]}")
                continue
            sample_req, sample_deliv = template_dashboard_examples(tpl)
            lines.append(f"• {key} — {action} OK (premium)")
            meta["offerings"].append(key)
            template_names.add(str(tpl.get("name") or key).lower())
            meta["examples"][key] = {"request": sample_req, "deliverable": sample_deliv}

        existing, _ = list_offerings()
        for row in existing or []:
            name = str(row.get("name") or "").strip()
            if not name or name.lower() in template_names:
                continue
            oid = str(row.get("id") or "")
            if not oid:
                continue
            spec = {
                "name": name.lower().replace(" ", "_"),
                "description": str(row.get("description") or name),
                "price_usd": float(row.get("priceValue") or 0),
                "sla_minutes": int(row.get("slaMinutes") or 15),
            }
            if spec["price_usd"] <= 0:
                continue
            payload = build_adhoc_payload(spec)
            payload.pop("service_kind", None)
            from aria_core.skills.acp_cli import update_offering

            updated, err = update_offering(
                oid,
                description=payload["description"],
                requirements=payload["requirements"],
                deliverable=payload["deliverable"],
                **_subscription_update_kw(payload),
            )
            if err or not updated:
                lines.append(f"• {name} — adhoc erreur {str(err)[:60]}")
                continue
            kind = _infer_service_kind(spec)
            sample_req, sample_deliv = premium_examples(kind, name)
            lines.append(f"• {name} — adhoc premium OK")
            meta["offerings"].append(name)
            meta["examples"][name] = {"request": sample_req, "deliverable": sample_deliv}

        if meta["examples"]:
            lines.extend(
                [
                    "",
                    "Exemples demande/livrable : auto-injectés (schema.examples — rafraîchir dashboard).",
                ]
            )
            for key, ex in meta["examples"].items():
                lines.append(f"— {key} ✓")
        return "\n".join(lines), meta

    if not template_key:
        if lang_key == "fr":
            return (
                "Précise le produit.\n"
                "Exemple : « lancer produit acp template veille_zhc_x1 et publier sur X »\n"
                "Réparer tout : « réparer offres acp »",
                {"acp": "product_launch_missing_template"},
            )
        return "Specify template key.", {"acp": "product_launch_missing_template"}

    template = resolve_template(template_key)
    if not template:
        return f"Template inconnu : {template_key}", {"acp": "product_launch_unknown"}

    try:
        payload = build_offering_payload(template, price_override=_parse_price_override(message))
        payload = _enrich_payload(template, payload)
    except ValueError as exc:
        return str(exc), {"acp": "product_launch_invalid"}

    row, action, err = await _upsert_offering(payload)
    if err or not row:
        return (err or "échec offering")[:400], {"acp": "product_launch_error"}

    promo = await _promote_product(
        name=payload["name"],
        description=payload["description"],
        price_usd=float(payload["price_value"]),
        sla_minutes=int(payload["sla_minutes"]),
        lang=lang_key,
        do_social=do_social,
    )

    off_id = row.get("id") or "?"
    if lang_key == "fr":
        lines = [
            f"Produit ACP {action} — {payload['name']}",
            f"ID : {off_id}",
            f"Prix : {payload['price_value']} USDC · SLA {payload['sla_minutes']}m",
            "Schémas requirements/deliverable : complets (dashboard Virtuals).",
        ]
        if do_social:
            lines.append("")
            lines.append("Promo :")
            lines.append(f"• X : {'publié' if promo.get('x_posted') else 'brouillon'}")
            if promo.get("tweet_text"):
                lines.append(f"  « {promo['tweet_text']} »")
            lines.append(
                f"• Telegram opérateur : {'notifié' if promo.get('telegram_notified') else 'non'}"
            )
        else:
            lines.append("Ajoute « et publier sur X » pour la promo réseaux.")
        body = "\n".join(lines)
    else:
        body = f"ACP product {action} — {payload['name']} @ {payload['price_value']} USDC"
    return body, {
        "acp": "product_launch",
        "action": action,
        "offering_id": off_id,
        "template": template_key,
        **promo,
    }