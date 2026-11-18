"""Email alerts — instant alerts for score >= instant threshold, daily digest for score >= digest threshold."""

import json
import logging
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from tracker.config import (
    ALERT_EMAIL_FROM,
    ALERT_EMAIL_TO,
    SMTP_HOST,
    SMTP_PORT,
    SMTP_USER,
    SMTP_PASSWORD,
)
from tracker.config import DIGEST_TOP_N, SEARCH_ZIP, SEARCH_RADIUS_MILES, SCORE_DAILY_DIGEST
from tracker.scorer import is_winter_penalized_trim, market_avg_for, score_breakdown

logger = logging.getLogger(__name__)

REGEN_NOTE = (
    "⚠️ <strong>Plug-in hybrid winter driving note:</strong> Regenerative braking on these "
    "PHEVs creates more deceleration when lifting off the throttle than a conventional car — "
    "on slippery surfaces this can feel abrupt for inexperienced drivers. "
    "Recommend a dedicated practice session in a snowy parking lot before solo winter driving."
)

TRIM_GUIDE_HTML = """
<table style="border-collapse:collapse;font-size:13px;width:100%">
  <thead>
    <tr style="background:#f0f0f0">
      <th style="padding:6px 10px;text-align:left;border:1px solid #ddd">Trim</th>
      <th style="padding:6px 10px;text-align:left;border:1px solid #ddd">Score bias</th>
      <th style="padding:6px 10px;text-align:left;border:1px solid #ddd">Why</th>
    </tr>
  </thead>
  <tbody>
    <tr><td style="padding:5px 10px;border:1px solid #ddd">High Altitude</td><td style="padding:5px 10px;border:1px solid #ddd">⬆⬆ Preferred</td><td style="padding:5px 10px;border:1px solid #ddd">All Sahara features + adaptive cruise + safety tech as standard. Best winter daily driver.</td></tr>
    <tr><td style="padding:5px 10px;border:1px solid #ddd">Sahara</td><td style="padding:5px 10px;border:1px solid #ddd">⬆ Preferred</td><td style="padding:5px 10px;border:1px solid #ddd">All-season tires, blind spot monitoring, on-road suspension. Top pick for this use case.</td></tr>
    <tr><td style="padding:5px 10px;border:1px solid #ddd">Rubicon X</td><td style="padding:5px 10px;border:1px solid #ddd">Neutral</td><td style="padding:5px 10px;border:1px solid #ddd">Strong hardware but you're paying for off-road gear that adds no winter benefit.</td></tr>
    <tr><td style="padding:5px 10px;border:1px solid #ddd">Sport S</td><td style="padding:5px 10px;border:1px solid #ddd">Neutral</td><td style="padding:5px 10px;border:1px solid #ddd">Decent safety features, simpler trim — good value if priced right.</td></tr>
    <tr style="background:#fff8f0"><td style="padding:5px 10px;border:1px solid #ddd">Willys / Willys '41</td><td style="padding:5px 10px;border:1px solid #ddd">⬇ Penalized</td><td style="padding:5px 10px;border:1px solid #ddd">Mud-terrain tires are actively worse on packed snow and ice than all-seasons.</td></tr>
    <tr style="background:#fff8f0"><td style="padding:5px 10px;border:1px solid #ddd">Rubicon</td><td style="padding:5px 10px;border:1px solid #ddd">⬇ Penalized</td><td style="padding:5px 10px;border:1px solid #ddd">Trail-tuned suspension + A/T tires = least predictable on winter pavement.</td></tr>
  </tbody>
</table>
"""


def _send_email(subject: str, html_body: str) -> bool:
    if not all([ALERT_EMAIL_TO, ALERT_EMAIL_FROM, SMTP_USER, SMTP_PASSWORD]):
        logger.warning("Email not configured — skipping send (subject: %s)", subject)
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = ALERT_EMAIL_FROM
    msg["To"] = ALERT_EMAIL_TO
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(ALERT_EMAIL_FROM, [ALERT_EMAIL_TO], msg.as_string())
        logger.info("Email sent: %s", subject)
        return True
    except Exception as e:
        logger.error("Failed to send email (%s): %s", subject, e)
        return False


def send_low_inventory_warning(mc_count: int, source_errors: list[str]) -> bool:
    """Send a warning email when MarketCheck returns suspiciously few listings."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    error_html = ""
    if source_errors:
        items = "".join(f"<li>{e}</li>" for e in source_errors)
        error_html = f"<p><strong>Source errors recorded:</strong><br><ul>{items}</ul></p>"

    body = f"""
<html><body style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;padding:20px">
<h2 style="color:#cc0000">⚠️ PHEV/4xe Deal Tracker — Low Inventory Warning</h2>
<p>The tracker run at <strong>{ts}</strong> returned only <strong>{mc_count} MarketCheck listings</strong>
across all tracked vehicles, which is below the expected minimum of 10.</p>
<p>This likely indicates one of:<br>
<ul>
  <li>MarketCheck API quota exhaustion</li>
  <li>API outage or rate limiting</li>
  <li>Search parameter issue</li>
</ul></p>
{error_html}
<p style="color:#666;font-size:12px">No deal digest was sent for this run. Check GitHub Actions logs for details.</p>
</body></html>
"""
    return _send_email("⚠️ PHEV/4xe Deal Tracker: Low inventory warning", body)


def send_vehicle_anomaly_warning(anomalies: list[dict]) -> bool:
    """Warn when one specific vehicle's MarketCheck count drops to 0 despite a
    healthy recent average — the aggregate low-inventory check above can miss
    this since the other tracked vehicles can mask a single one going to 0."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    rows = "".join(
        f"<li><strong>{a['vehicle']}</strong>: 0 listings this run "
        f"(recent {a['lookback']}-run average: {a['baseline']:.1f})</li>"
        for a in anomalies
    )
    body = f"""
<html><body style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;padding:20px">
<h2 style="color:#cc0000">⚠️ PHEV/4xe Deal Tracker — Vehicle Anomaly Warning</h2>
<p>The tracker run at <strong>{ts}</strong> found <strong>0 MarketCheck listings</strong> for the
following vehicle(s), despite a healthy recent average:</p>
<ul>{rows}</ul>
<p>This is the same class of issue previously found with Grand Cherokee 4xe, Tucson PHEV, and
RAV4 Prime — a model/powertrain query silently stopped matching. Worth re-verifying the
<code>mc_model</code> / <code>mc_powertrain_type</code> for the affected vehicle(s) in
<code>tracker/config.py</code> against the live MarketCheck API.</p>
<p style="color:#666;font-size:12px">The run continued normally for all other vehicles — this is
informational, not a failure.</p>
</body></html>
"""
    return _send_email("⚠️ PHEV/4xe Deal Tracker: Vehicle anomaly warning", body)


def _format_price(price: int | None) -> str:
    if price is None:
        return "N/A"
    return f"${price:,}"


def _score_label(score: float) -> str:
    if score >= 80:
        return "⭐ GREAT DEAL"
    if score >= 65:
        return "✅ GOOD DEAL"
    return "📋 FAIR"


def _listing_card_html(lst: dict[str, Any], market_avg: float | None) -> str:
    score = lst.get("composite_score") or 0
    price = lst.get("price")
    trim = lst.get("trim") or ""
    year = lst.get("year") or ""
    make = lst.get("make") or ""
    model = lst.get("model") or "Wrangler 4xe"

    pct_str = ""
    if market_avg and price and market_avg > 0:
        diff = market_avg - price
        word = "under" if diff >= 0 else "over"
        pct_str = (f" — ${abs(diff):,.0f} {word} fair price of {_format_price(int(market_avg))}"
                   f" for its mileage")

    location = ", ".join(filter(None, [lst.get("city"), lst.get("state")]))
    dealer_name = lst.get("dealer_name") or ""
    dealer_rating = lst.get("dealer_rating")
    dealer_str = dealer_name
    if dealer_rating:
        dealer_str += f" ★ {dealer_rating:.1f}"

    dom = lst.get("days_on_market")
    dom_str = f"{dom} day{'s' if dom != 1 else ''} ago" if dom is not None else "Unknown"

    ref_price = lst.get("ref_price") or 0
    ref_html = ""
    if ref_price and lst.get("price"):
        pct = (ref_price - lst["price"]) / ref_price * 100
        if pct >= 1:
            ref_html = f"✅ {pct:.1f}% below MC reference ({_format_price(int(ref_price))})"
        elif pct <= -5:
            ref_html = f"⚠️ {abs(pct):.1f}% above MC reference ({_format_price(int(ref_price))})"
        else:
            ref_html = f"At market reference ({_format_price(int(ref_price))})"

    carfax_parts = []
    if lst.get("no_accidents"):
        carfax_parts.append("✅ No accidents (verified)")
    elif lst.get("clean_title"):
        carfax_parts.append("✅ Clean title (accidents unknown)")
    if lst.get("one_owner"):
        carfax_parts.append("✅ 1 owner")
    carfax_html = "  ".join(carfax_parts) if carfax_parts else "Unknown — pull a report"

    winter_kit = ""
    if lst.get("cold_weather_group"):
        winter_kit = "🧤 Cold Weather Group (heated seats, heated steering wheel, remote start)"

    safety_parts = []
    if lst.get("has_blind_spot_mon"):
        safety_parts.append("✅ Blind spot monitoring")
    safety_html = "  ".join(safety_parts) if safety_parts else ""

    sources_list = []
    try:
        sources_list = json.loads(lst.get("sources") or "[]")
    except Exception:
        pass

    # Price drop
    price_drop_html = ""
    try:
        history = json.loads(lst.get("price_history") or "[]")
        if len(history) >= 1 and price:
            orig = history[0]["price"]
            drop = orig - price
            if drop > 0:
                price_drop_html = f"↓ ${drop:,} on {history[-1]['date']}"
    except Exception:
        pass

    listing_url = lst.get("listing_url") or "#"

    # Winter penalty note
    winter_penalty = ""
    if is_winter_penalized_trim(model, trim):
        winter_penalty = "<tr><td style='padding:4px 0;color:#c00'><strong>⚠️ Mud-terrain tires</strong> — less suitable for winter pavement driving</td></tr>"

    # Score breakdown pills
    breakdown = score_breakdown(lst, market_avg)
    breakdown_pills = ""
    for label, pts in breakdown:
        if pts > 0:
            color = "#1a7a1a" if pts >= 8 else "#2a7a2a"
            bg = "#e6f4e6" if pts >= 8 else "#f0f8f0"
            pts_str = f"+{pts:.0f}"
        elif pts < 0:
            color = "#990000"
            bg = "#fdf0f0"
            pts_str = f"{pts:.0f}"
        else:
            continue
        breakdown_pills += (
            f'<span style="display:inline-block;background:{bg};color:{color};'
            f'border:1px solid {color}33;border-radius:12px;padding:2px 8px;'
            f'margin:2px 3px 2px 0;font-size:11px;white-space:nowrap">'
            f'{label} <strong>{pts_str}</strong></span>'
        )

    return f"""
<div style="background:#f9f9f9;border:1px solid #ddd;border-radius:6px;padding:16px;margin:16px 0;font-family:sans-serif">
  <div style="font-size:16px;font-weight:bold;margin-bottom:8px">
    [{score:.0f}/100] {_score_label(score)}
  </div>
  {f'<div style="margin-bottom:10px;line-height:1.8">{breakdown_pills}</div>' if breakdown_pills else ""}
  <div style="font-size:18px;font-weight:bold;margin-bottom:12px">{year} {make} {model} {trim}</div>
  <table style="font-size:14px;border-collapse:collapse;width:100%">
    <tr><td style="padding:4px 0;color:#666;width:130px">Price</td><td style="padding:4px 0"><strong>{_format_price(price)}</strong>{pct_str}</td></tr>
    <tr><td style="padding:4px 0;color:#666">Mileage</td><td style="padding:4px 0">{f"{lst['mileage']:,} miles" if lst.get("mileage") is not None else "N/A"}</td></tr>
    <tr><td style="padding:4px 0;color:#666">Location</td><td style="padding:4px 0">{location}</td></tr>
    <tr><td style="padding:4px 0;color:#666">Dealer</td><td style="padding:4px 0">{dealer_str}</td></tr>
    <tr><td style="padding:4px 0;color:#666">Listed</td><td style="padding:4px 0">{dom_str}</td></tr>
    {f'<tr><td style="padding:4px 0;color:#666">MC rating</td><td style="padding:4px 0">{ref_html}</td></tr>' if ref_html else ""}
    <tr><td style="padding:4px 0;color:#666">History</td><td style="padding:4px 0">{carfax_html}</td></tr>
    {f'<tr><td style="padding:4px 0;color:#666">Winter kit</td><td style="padding:4px 0">{winter_kit}</td></tr>' if winter_kit else ""}
    {f'<tr><td style="padding:4px 0;color:#666">Safety tech</td><td style="padding:4px 0">{safety_html}</td></tr>' if safety_html else ""}
    <tr><td style="padding:4px 0;color:#666">Sources</td><td style="padding:4px 0">{", ".join(sources_list)}</td></tr>
    {f'<tr><td style="padding:4px 0;color:#666">Price drop</td><td style="padding:4px 0;color:#090">{price_drop_html}</td></tr>' if price_drop_html else ""}
    {winter_penalty}
  </table>
  <div style="margin-top:12px">
    <a href="{listing_url}" style="background:#1a6fc4;color:#fff;padding:8px 14px;border-radius:4px;text-decoration:none;font-size:13px">View Listing →</a>
  </div>
</div>"""


def send_instant_alerts(listings: list[dict[str, Any]], market_avgs: dict) -> list[str]:
    """Send one email per great deal listing. Returns the VINs actually sent,
    so a failed send in the middle doesn't mark the wrong cars alerted."""
    sent: list[str] = []
    for lst in listings:
        trim = lst.get("trim") or ""
        year = lst.get("year") or ""
        make = lst.get("make") or ""
        model = lst.get("model") or ""
        price = lst.get("price")
        score = lst.get("composite_score") or 0

        market_avg = market_avg_for(lst, market_avgs)
        pct_below = ""
        if market_avg and price:
            pct = (market_avg - price) / market_avg * 100
            pct_below = f" ({abs(pct):.0f}% {'below' if pct >= 0 else 'above'} fair price)"

        cwg = " + Cold Weather Group" if lst.get("cold_weather_group") else ""
        subject = f"🚨 Great Deal: {year} {make} {model} {trim} — {_format_price(price)}{pct_below}{cwg}"

        body = f"""
<html><body style="font-family:sans-serif;max-width:640px;margin:0 auto">
  <h2 style="color:#c00">🚨 Great Deal Alert</h2>
  <p style="color:#666">Good deals at this price move in 24–72 hours.</p>
  {_listing_card_html(lst, market_avg)}
  <hr style="margin:24px 0">
  <p style="font-size:13px;color:#555;background:#fffbe6;padding:12px;border-radius:4px">{REGEN_NOTE}</p>
  <p style="font-size:11px;color:#999;margin-top:24px">PHEV/4xe Deal Tracker · ZIP {SEARCH_ZIP} · {SEARCH_RADIUS_MILES} mi radius · Score: {score:.0f}/100</p>
</body></html>"""

        if _send_email(subject, body):
            sent.append(lst["vin"])

    return sent


def _compact_row(lst: dict[str, Any], market_avg: float | None, note: str = "") -> str:
    """One-line table row for a listing (digest sections beyond the top N)."""
    price = lst.get("price")
    under = ""
    if market_avg and price:
        diff = market_avg - price
        under = f"${abs(diff):,.0f} {'under' if diff >= 0 else 'over'}"
    miles = f"{lst['mileage']:,}" if lst.get("mileage") is not None else "?"
    location = ", ".join(filter(None, [lst.get("city"), lst.get("state")]))
    url = lst.get("listing_url") or "#"
    note_html = f' <span style="color:#090">{note}</span>' if note else ""
    td = "padding:4px 6px;border-bottom:1px solid #eee"
    return (
        f"<tr><td style='{td}'><a href='{url}'>{lst.get('year') or ''} {lst.get('model') or ''} {lst.get('trim') or ''}</a>"
        f"{note_html}</td>"
        f"<td style='{td}'>{_format_price(price)}</td><td style='{td}'>{under}</td>"
        f"<td style='{td}'>{miles}</td><td style='{td}'>{lst.get('dealer_name') or ''}"
        f"{f' ({location})' if location else ''}</td>"
        f"<td style='{td}'>{(lst.get('composite_score') or 0):.0f}</td></tr>"
    )


def _compact_table(rows_html: str) -> str:
    th = "padding:4px 6px;text-align:left;border-bottom:2px solid #ccc"
    return (
        "<table style='font-size:13px;border-collapse:collapse;width:100%'>"
        f"<tr><th style='{th}'>Car</th><th style='{th}'>Price</th><th style='{th}'>vs. fair</th>"
        f"<th style='{th}'>Miles</th><th style='{th}'>Dealer</th><th style='{th}'>Score</th></tr>"
        f"{rows_html}</table>"
    )


def send_daily_digest(
    good_deals: list[dict[str, Any]],
    market_avgs: dict,
    snapshot: dict,
    stats: dict,
    source_status: dict,
    gone: list[dict[str, Any]] | None = None,
) -> bool:
    """Daily digest, ordered for a quick skim: what changed since the last
    run, then the top-N shortlist as full cards, then the rest as one-line
    rows, then the market snapshot."""
    gone = gone or []
    today = datetime.now(timezone.utc).strftime("%b %d, %Y")
    n = len(good_deals)
    subject = f"🔌 PHEV/4xe Digest — {today} — {n} deal{'s' if n != 1 else ''} worth seeing"

    sorted_deals = sorted(good_deals, key=lambda l: l.get("composite_score") or 0, reverse=True)
    top = sorted_deals[:DIGEST_TOP_N]
    rest = sorted_deals[DIGEST_TOP_N:]

    # --- Since the last run ---
    change_rows = ""
    for lst in sorted_deals:
        note = ""
        if lst.get("_is_new"):
            note = "NEW"
        elif lst.get("_price_cut_from") and lst.get("price"):
            note = f"CUT ↓${lst['_price_cut_from'] - lst['price']:,}"
        if note:
            change_rows += _compact_row(lst, market_avg_for(lst, market_avgs), note)
    gone_rows = "".join(
        _compact_row(lst, None, "ALERTED" if lst.get("alerted") else "") for lst in gone
    )
    changes_html = ""
    if change_rows or gone_rows:
        changes_html = "<h2>Since the last run</h2>"
        if change_rows:
            changes_html += "<p style='margin-bottom:4px'><strong>New or price-cut deals</strong></p>" + _compact_table(change_rows)
        if gone_rows:
            changes_html += ("<p style='margin-bottom:4px'><strong>Gone (sold or delisted)</strong> — "
                             "previously alerted or digest-worthy</p>" + _compact_table(gone_rows))

    # --- Top N cards + compact rest ---
    cards_html = "".join(_listing_card_html(lst, market_avg_for(lst, market_avgs)) for lst in top)
    rest_html = ""
    if rest:
        rest_html = (f"<h3>Also worth a look ({len(rest)})</h3>"
                     + _compact_table("".join(_compact_row(l, market_avg_for(l, market_avgs)) for l in rest)))
    no_deals_msg = ""
    if not good_deals:
        no_deals_msg = f"<p style='color:#888'>No listings scored ≥ {SCORE_DAILY_DIGEST} this run. Check back next time.</p>"

    # --- Market snapshot (active listings only) ---
    avg_rows = ""
    for (model, trim), avg in sorted(snapshot.get("avg_by_model_trim", {}).items()):
        avg_rows += f"<tr><td style='padding:3px 8px'>{model} {trim}</td><td style='padding:3px 8px'>{_format_price(int(avg))}</td></tr>"

    price_trend_rows = ""
    from tracker.store import get_price_trend
    for (model, trim), data in sorted(get_price_trend(7).items()):
        t, old = data.get("today"), data.get("7d_ago")
        if t and old:
            diff = t - old
            arrow = "⬆️" if diff > 200 else ("⬇️" if diff < -200 else "➡️")
            price_trend_rows += f"<tr><td style='padding:3px 8px'>{model} {trim}</td><td style='padding:3px 8px'>{arrow} {_format_price(int(abs(diff)))} {'up' if diff > 0 else 'down'} vs. 7d ago (same cars)</td></tr>"

    count_items = "".join(
        f"<li>{v}: <strong>{c}</strong>{' ⚠️ none found — check the query with scripts/probe_vehicle.py' if c == 0 else ''}</li>"
        for v, c in sorted(snapshot.get("vehicle_counts", {}).items())
    )
    lowest_rows = "".join(
        f"<li>{model}: <strong>{_format_price(price)}</strong></li>"
        for model, price in sorted(snapshot.get("lowest_by_model", {}).items())
    )
    source_status_html = " · ".join(f"{'✅' if ok else '❌'} {src}" for src, ok in source_status.items())

    snapshot_html = f"""
<div style="background:#f0f4ff;border:1px solid #c0d0f0;border-radius:6px;padding:16px;margin:24px 0">
  <h3 style="margin-top:0">📊 Market Snapshot</h3>
  <p>Listed today ({SEARCH_RADIUS_MILES} mi): <strong>{snapshot.get("total", 0)}</strong>
     · verified no accidents + 1 owner: <strong>{snapshot.get("verified_clean_count", 0)}</strong></p>
  {f"<ul style='margin-top:0'>{count_items}</ul>" if count_items else ""}
  {f"<p>Lowest price by model today:</p><ul>{lowest_rows}</ul>" if lowest_rows else ""}
  <table style="font-size:13px;border-collapse:collapse">
    <tr><th style="padding:3px 8px;text-align:left">Model / trim</th><th style="padding:3px 8px;text-align:left">Avg price</th></tr>
    {avg_rows}
  </table>
  {f'<table style="font-size:13px;border-collapse:collapse;margin-top:8px">{price_trend_rows}</table>' if price_trend_rows else ""}
  <p style="font-size:11px;color:#777;margin-bottom:0">Sources: {source_status_html}</p>
</div>"""

    body = f"""
<html><body style="font-family:sans-serif;max-width:640px;margin:0 auto;padding:20px">
  <h1 style="font-size:22px">🔌 PHEV/4xe Deal Digest</h1>
  <p style="color:#666">{today} · ZIP {SEARCH_ZIP} · {SEARCH_RADIUS_MILES} mi radius · {stats.get("new", 0)} new listings · {stats.get("price_drops", 0)} price drops</p>
  {changes_html}
  <h2>Top {len(top)} of {n} deals worth seeing</h2>
  {cards_html}
  {rest_html}
  {no_deals_msg}
  {snapshot_html}
  <p style="font-size:13px;color:#555;background:#fffbe6;padding:12px;border-radius:4px">{REGEN_NOTE}</p>
  <h3 style="margin-top:24px">Jeep 4xe trim guide — winter daily driver scoring</h3>
  <p style="font-size:12px;color:#777">Grand Cherokee 4xe, Outlander PHEV, Tucson PHEV, and RAV4 Prime ship 4WD/AWD
  standard on every trim, so their trim scoring is a lighter nudge (wheel size, standard heated packages) rather
  than this Wrangler table — see <code>scorer.py</code> for those weights.</p>
  {TRIM_GUIDE_HTML}
  <p style="font-size:11px;color:#999;margin-top:24px">PHEV/4xe Deal Tracker · Runs daily · Scores calibrated for teen driver, Chicago winter conditions · "Fair price" = market price for that trim/year adjusted for mileage</p>
</body></html>"""

    return _send_email(subject, body)
